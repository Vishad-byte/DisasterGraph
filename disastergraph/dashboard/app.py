from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from disastergraph.config import get_neo4j_connection, get_settings
from disastergraph.agent.runner import run_detection_cycle_once
from disastergraph.graph.road_geometry import get_road_geometry
from disastergraph.graph.router import RoadRouter, decode_polyline


app = FastAPI(title="DisasterGraph Dashboard API", version="0.1.0")
settings = get_settings()
LOGGER = logging.getLogger(__name__)

# Include ingestion router so /ingest/* endpoints are served directly by unified process
from disastergraph.ingestion.main import app as _ingestion_fastapi_app
app.include_router(_ingestion_fastapi_app.router)
_OVERVIEW_TTL_SECONDS = 300.0
_ROUTES_TTL_SECONDS = 900.0
_overview_cache_lock = threading.Lock()
_overview_cache_payload: dict[str, Any] | None = None
_overview_cache_ts = 0.0
_routes_cache_lock = threading.Lock()
_routes_cache_payload: list[dict[str, Any]] | None = None
_routes_cache_ts = 0.0


def _frontend_dist_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def _ingestion_url(path: str) -> str:
    base = settings.ingestion_service_url.strip().rstrip("/")
    return f"{base}{path}"


def _http_get_json(url: str) -> dict[str, Any]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail=f"Invalid response from upstream GET {url}")
        return payload


def _http_post_json(
    url: str,
    timeout_seconds: float = 120.0,
    retries: int = 2,
    retry_delay_seconds: float = 1.0,
) -> dict[str, Any]:
    attempts = max(1, retries + 1)
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                resp = client.post(url)
                resp.raise_for_status()
                payload = resp.json()
                if not isinstance(payload, dict):
                    raise HTTPException(
                        status_code=502,
                        detail=f"Invalid response from upstream POST {url}",
                    )
                return payload
        except httpx.HTTPStatusError as exc:
            last_error = exc
            status_code = exc.response.status_code
            if status_code not in {502, 503, 504} or attempt >= attempts - 1:
                raise
            time.sleep(retry_delay_seconds)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt >= attempts - 1:
                raise
            time.sleep(retry_delay_seconds)

    if last_error is not None:
        raise last_error
    raise HTTPException(status_code=502, detail=f"Upstream POST failed for {url}")


def _is_standalone_ingestion() -> bool:
    url = settings.ingestion_service_url.strip().lower()
    return url != "internal" and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost")


def _dispatch_ingest(task_name: str) -> dict[str, Any]:
    if _is_standalone_ingestion():
        try:
            return _http_post_json(_ingestion_url(f"/ingest/{task_name}"))
        except Exception as exc:
            LOGGER.warning("HTTP proxy to standalone ingestion service failed (%s), running in-process: %s", task_name, exc)

    from disastergraph.ingestion.main import (
        poll_air_quality,
        poll_eonet,
        poll_firms,
        poll_gdacs,
        poll_open_meteo,
        poll_prune,
        poll_sentinel,
        poll_usgs,
        ingest_osm_resources,
        ingest_osm_roads,
        ingest_run_all,
        trigger_scheduler_jobs,
    )
    if task_name == "firms":
        return poll_firms()
    elif task_name == "usgs":
        return poll_usgs()
    elif task_name == "eonet":
        return poll_eonet()
    elif task_name == "gdacs":
        return poll_gdacs()
    elif task_name == "open_meteo":
        return poll_open_meteo()
    elif task_name == "air_quality":
        return poll_air_quality()
    elif task_name == "sentinel":
        return poll_sentinel()
    elif task_name == "osm_roads":
        return ingest_osm_roads(_conn())
    elif task_name == "osm_resources":
        return ingest_osm_resources(_conn())
    elif task_name == "prune":
        return poll_prune()
    elif task_name == "run_all":
        return ingest_run_all()
    elif task_name == "scheduler/trigger":
        return trigger_scheduler_jobs()
    raise HTTPException(status_code=404, detail=f"Unknown ingest task: {task_name}")


def _dispatch_ingest_health() -> dict[str, Any]:
    if _is_standalone_ingestion():
        try:
            return _http_get_json(_ingestion_url("/ingest/health"))
        except Exception:
            pass
    try:
        from disastergraph.ingestion.main import ingest_health
        return ingest_health()
    except Exception as exc:
        return {"status": "ok", "mode": "in_process", "detail": str(exc)}


@app.on_event("startup")
def _dashboard_startup() -> None:
    if not _is_standalone_ingestion():
        try:
            from disastergraph.ingestion.main import startup_event as ingestion_startup
            ingestion_startup()
            LOGGER.info("Unified mode: Ingestion background scheduler started in-process.")
        except Exception as exc:
            LOGGER.warning("Could not start unified in-process ingestion scheduler: %s", exc)


@app.on_event("shutdown")
def _dashboard_shutdown() -> None:
    if not _is_standalone_ingestion():
        try:
            from disastergraph.ingestion.main import shutdown_event as ingestion_shutdown
            ingestion_shutdown()
        except Exception:
            pass


def _conn() -> Any:
    return get_neo4j_connection(settings)


def _as_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        attrs = item.get("attributes", {}) if "attributes" in item else item
        if not isinstance(attrs, dict):
            continue
        row = dict(attrs)
        if "v_id" in item:
            row.setdefault("id", item.get("v_id"))
        out.append(row)
    return out


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _zone_points(conn: Any) -> list[dict[str, Any]]:
    rows = _as_rows(conn.getVertices("Zone", limit=10000))
    points: list[dict[str, Any]] = []
    for row in rows:
        lat = _to_float(row.get("centroid_lat"))
        lng = _to_float(row.get("centroid_lng"))
        if lat is None or lng is None:
            continue
        points.append(
            {
                "id": str(row.get("id", "")),
                "name": str(row.get("name", row.get("id", ""))),
                "lat": lat,
                "lng": lng,
                "severity": float(row.get("disaster_severity", 0) or 0),
                "is_affected": _to_bool(row.get("is_affected", False)),
            }
        )
    return points


def _active_events_with_affected_zones(conn: Any) -> list[dict[str, Any]]:
    events = conn.getVertices("DisasterEvent", limit=10000)
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        attrs = event.get("attributes", {})
        if not isinstance(attrs, dict) or attrs.get("status") != "active":
            continue
        event_id = str(event.get("v_id") or attrs.get("event_id") or "")
        if not event_id:
            continue
        affected_edges = conn.getEdges("DisasterEvent", event_id, "affects")
        source_val = str(attrs.get("source") or attrs.get("satellite_source") or "unknown")
        rows.append(
            {
                "event_id": event_id,
                "event_type": str(attrs.get("event_type", "unknown")),
                "severity": float(attrs.get("severity", 0) or 0),
                "timestamp": attrs.get("timestamp"),
                "satellite_source": source_val,
                "source": source_val,
                "ingested_at": attrs.get("ingested_at"),
                "lat": attrs.get("lat", attrs.get("latitude")),
                "lon": attrs.get("lon", attrs.get("longitude")),
                "affected_zone_ids": [
                    str(edge.get("to_id"))
                    for edge in affected_edges
                    if isinstance(edge, dict) and edge.get("to_id")
                ],
            }
        )
    rows.sort(key=lambda x: float(x.get("severity", 0) or 0), reverse=True)
    return rows


def _route_segments(conn: Any) -> list[dict[str, Any]]:
    zones = _zone_points(conn)
    by_zone_id = {str(zone.get("id", "")): zone for zone in zones}
    segments: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for from_zone_id in by_zone_id:
        edges = conn.getEdges("Zone", from_zone_id, "connects")
        for edge in edges:
            if not isinstance(edge, dict):
                continue

            to_zone_id = str(edge.get("to_id") or "")
            if not to_zone_id:
                continue

            from_zone = by_zone_id.get(from_zone_id)
            to_zone = by_zone_id.get(to_zone_id)
            if not from_zone or not to_zone:
                continue

            attrs = edge.get("attributes", {}) if isinstance(edge.get("attributes", {}), dict) else {}
            route_id = str(attrs.get("route_id") or edge.get("e_id") or f"{from_zone_id}->{to_zone_id}")
            dedupe_key = tuple(sorted([from_zone_id, to_zone_id]) + [route_id])
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            raw_blocked = _to_bool(attrs.get("is_blocked", False))
            raw_reason = str(attrs.get("blockage_reason") or "").strip()
            explicit_status = str(attrs.get("status") or "").strip().lower()
            low_reason = raw_reason.lower()

            # Determine hazard type
            hazard_type = "none"
            if any(k in low_reason for k in ["pollution", "no2", "pm2", "gas", "plume", "smog"]):
                hazard_type = "pollution"
            elif any(k in low_reason for k in ["flood", "water", "inundat", "submerg"]):
                hazard_type = "flood"
            elif any(k in low_reason for k in ["fire", "wildfire", "thermal", "burn"]):
                hazard_type = "fire"
            elif any(k in low_reason for k in ["earthquake", "quake", "tremor"]):
                hazard_type = "earthquake"
            elif from_zone.get("is_affected") or to_zone.get("is_affected"):
                hazard_type = "pollution"

            # Determine 3-state road status:
            # 1. Closed: physically impassable (flood, fire, structural collapse, demo blockage)
            is_physically_closed = (
                explicit_status == "closed"
                or (raw_blocked and any(
                    k in low_reason for k in ["flood", "collapse", "structural", "debris", "fire", "water", "demo blockage", "closed", "blocked"]
                ) and not any(k in low_reason for k in ["pollution", "gas plume", "no2", "smog"]))
            )

            # 2. Avoid: passable but hazardous (gas plumes, high air pollution, speed reduction)
            is_avoid = (
                not is_physically_closed and (
                    explicit_status == "avoid"
                    or raw_blocked
                    or any(k in low_reason for k in ["pollution", "no2", "pm2", "gas", "plume", "hazard", "smog", "avoid", "speed reduced"])
                    or (from_zone.get("is_affected") and float(from_zone.get("severity") or 0) >= 0.5)
                    or (to_zone.get("is_affected") and float(to_zone.get("severity") or 0) >= 0.5)
                )
            )

            if is_physically_closed:
                status = "closed"
                status_label = "Closed (Physically Blocked)"
                reason = raw_reason or "Physically impassable corridor due to structural/flood hazard"
            elif is_avoid:
                status = "avoid"
                status_label = "Avoid (Hazardous / Poor Air Quality)"
                reason = raw_reason or "Passable corridor with high air pollution nearby: delay penalty applied"
            else:
                status = "clear"
                status_label = "Clear (Safe)"
                reason = "Safe and passable corridor with normal traffic flow"

            from_name = from_zone.get("name") or from_zone_id
            to_name = to_zone.get("name") or to_zone_id
            road_name = f"Corridor {from_name} ↔ {to_name}"
            path_coords = get_road_geometry(
                from_zone_id,
                to_zone_id,
                from_zone["lat"],
                from_zone["lng"],
                to_zone["lat"],
                to_zone["lng"],
            )

            segments.append(
                {
                    "route_id": route_id,
                    "from_zone_id": from_zone_id,
                    "to_zone_id": to_zone_id,
                    "road_name": road_name,
                    "from": {
                        "lat": from_zone["lat"],
                        "lng": from_zone["lng"],
                    },
                    "to": {
                        "lat": to_zone["lat"],
                        "lng": to_zone["lng"],
                    },
                    "path": path_coords,
                    "distance_km": _to_float(attrs.get("distance_km")),
                    "estimated_time_min": _to_float(attrs.get("estimated_time_min")),
                    "is_blocked": is_physically_closed,
                    "status": status,
                    "status_label": status_label,
                    "blockage_reason": reason,
                    "hazard_type": hazard_type,
                }
            )

    return segments


def _overview_payload(conn: Any, include_assignments: bool = False) -> dict[str, Any]:
    zones = _zone_points(conn)
    active_events = _active_events_with_affected_zones(conn)

    resources_raw = _as_rows(conn.getVertices("Resource", limit=10000))
    available_resources = 0
    resources_by_type: dict[str, int] = {}
    resources_inventory: dict[str, dict[str, int]] = {}
    resources_list: list[dict[str, Any]] = []

    for resource in resources_raw:
        is_available = _to_bool(resource.get("is_available", False))
        if is_available:
            available_resources += 1
        r_type = str(resource.get("resource_type", "unknown") or "unknown").lower()
        resources_by_type[r_type] = resources_by_type.get(r_type, 0) + 1
        if r_type not in resources_inventory:
            resources_inventory[r_type] = {"total": 0, "available": 0}
        resources_inventory[r_type]["total"] += 1
        if is_available:
            resources_inventory[r_type]["available"] += 1

        lat = _to_float(resource.get("location_lat"))
        lng = _to_float(resource.get("location_lng"))
        res_id = str(resource.get("res_id") or resource.get("id") or "")
        if lat is not None and lng is not None:
            resources_list.append(
                {
                    "id": res_id,
                    "name": str(resource.get("name") or res_id),
                    "resource_type": r_type,
                    "lat": lat,
                    "lng": lng,
                    "is_available": is_available,
                    "capacity": resource.get("capacity"),
                    "current_load": resource.get("current_load", 0),
                }
            )

    assignments: list[dict[str, Any]] = []
    if include_assignments:
        assignments_resp = assignments_live()
        assignments = assignments_resp.get("assignments", [])

    affected_zone_count = len([z for z in zones if z.get("is_affected")])

    routes = _route_segments_cached(conn)
    routes_clear = len([r for r in routes if r.get("status") == "clear"])
    routes_avoid = len([r for r in routes if r.get("status") == "avoid"])
    routes_closed = len([r for r in routes if r.get("status") == "closed" or r.get("is_blocked")])

    cfg = get_settings()

    return {
        "zones": zones,
        "active_events": active_events,
        "resources": resources_list,
        "assignments": assignments,
        "meta": {
            "zone_count": len(zones),
            "affected_zone_count": affected_zone_count,
            "active_event_count": len(active_events),
            "assignment_count": len(assignments),
            "resource_count": len(resources_raw),
            "available_resource_count": available_resources,
            "resources_by_type": resources_by_type,
            "resources_inventory": resources_inventory,
            "routes_total_count": len(routes),
            "routes_clear_count": routes_clear,
            "routes_avoid_count": routes_avoid,
            "routes_closed_count": routes_closed,
            "data_mode": getattr(cfg, "data_mode", "live").lower(),
            "last_updated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def _invalidate_overview_cache() -> None:
    global _overview_cache_payload, _overview_cache_ts
    with _overview_cache_lock:
        _overview_cache_payload = None
        _overview_cache_ts = 0.0


def _invalidate_routes_cache() -> None:
    global _routes_cache_payload, _routes_cache_ts
    with _routes_cache_lock:
        _routes_cache_payload = None
        _routes_cache_ts = 0.0
    try:
        import disastergraph.graph.road_geometry as rg
        rg._ROAD_GEOMETRIES_CACHE = None
    except Exception:
        pass


def _invalidate_ui_caches() -> None:
    _invalidate_overview_cache()
    _invalidate_routes_cache()


def _overview_payload_cached(conn: Any) -> dict[str, Any]:
    global _overview_cache_payload, _overview_cache_ts
    now = time.monotonic()
    with _overview_cache_lock:
        if (
            _overview_cache_payload is not None
            and now - _overview_cache_ts <= _OVERVIEW_TTL_SECONDS
        ):
            return _overview_cache_payload
        if _overview_cache_payload is not None:
            return _overview_cache_payload

        payload = _overview_payload(conn, include_assignments=False)
        _overview_cache_payload = payload
        _overview_cache_ts = time.monotonic()
        return payload


def _route_segments_cached(conn: Any) -> list[dict[str, Any]]:
    global _routes_cache_payload, _routes_cache_ts
    now = time.monotonic()
    with _routes_cache_lock:
        if _routes_cache_payload is not None and now - _routes_cache_ts <= _ROUTES_TTL_SECONDS:
            return _routes_cache_payload
        if _routes_cache_payload is not None:
            return _routes_cache_payload

        payload = _route_segments(conn)
        _routes_cache_payload = payload
        _routes_cache_ts = time.monotonic()
        return payload


frontend_dist = _frontend_dist_dir()
app.mount(
    "/ui/assets",
    StaticFiles(directory=str(frontend_dist / "assets"), check_dir=False),
    name="ui-assets",
)


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/ui")


@app.get("/ui", response_class=HTMLResponse)
def ui_root() -> HTMLResponse:
    index_file = frontend_dist / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail="Frontend build not found. Run npm install && npm run build in frontend/.",
        )
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/ui/{path:path}", response_class=HTMLResponse)
def ui_spa_fallback(path: str) -> HTMLResponse:
    index_file = frontend_dist / "index.html"
    if not index_file.exists():
        raise HTTPException(
            status_code=404,
            detail="Frontend build not found. Run npm install && npm run build in frontend/.",
        )
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.get("/graph/snapshot")
def graph_snapshot() -> dict[str, Any]:
    try:
        conn = _conn()
        zones = _as_rows(conn.getVertices("Zone", limit=10000))
        resources = _as_rows(conn.getVertices("Resource", limit=10000))
        events = _as_rows(conn.getVertices("DisasterEvent", limit=10000))
        active_events = [e for e in events if str(e.get("status", "")) == "active"]

        return {
            "zones": zones,
            "resources": resources,
            "active_events": active_events,
            "meta": {
                "zone_count": len(zones),
                "resource_count": len(resources),
                "active_event_count": len(active_events),
            },
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/events/active")
def events_active() -> dict[str, Any]:
    try:
        conn = _conn()
        events = conn.getVertices("DisasterEvent", limit=10000)

        rows: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            attrs = event.get("attributes", {})
            if not isinstance(attrs, dict) or attrs.get("status") != "active":
                continue

            event_id = str(event.get("v_id") or attrs.get("event_id") or "")
            if not event_id:
                continue

            affected = conn.getEdges("DisasterEvent", event_id, "affects")
            rows.append(
                {
                    "event_id": event_id,
                    "event_type": attrs.get("event_type"),
                    "severity": attrs.get("severity"),
                    "satellite_source": attrs.get("satellite_source"),
                    "timestamp": attrs.get("timestamp"),
                    "affected_zone_count": len(affected),
                }
            )

        rows.sort(key=lambda x: float(x.get("severity", 0) or 0), reverse=True)
        return {"events": rows, "count": len(rows)}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/map/data")
def map_data() -> dict[str, Any]:
    try:
        overview = _overview_payload_cached(_conn())
        return {
            "zones": overview.get("zones", []),
            "active_events": overview.get("active_events", []),
            "meta": {
                "zone_count": int(overview.get("meta", {}).get("zone_count", 0)),
                "affected_zone_count": int(overview.get("meta", {}).get("affected_zone_count", 0)),
                "active_event_count": int(overview.get("meta", {}).get("active_event_count", 0)),
            },
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/ui-api/overview")
def ui_overview() -> dict[str, Any]:
    try:
        return _overview_payload_cached(_conn())
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/ui-api/assignments")
def ui_assignments() -> dict[str, Any]:
    return assignments_live()


@app.get("/ui-api/routes")
def ui_routes() -> dict[str, Any]:
    try:
        routes = _route_segments_cached(_conn())
        closed_count = len([route for route in routes if route.get("status") == "closed" or route.get("is_blocked")])
        avoid_count = len([route for route in routes if route.get("status") == "avoid"])
        clear_count = len([route for route in routes if route.get("status") == "clear"])
        return {
            "routes": routes,
            "count": len(routes),
            "blocked_count": closed_count,
            "closed_count": closed_count,
            "avoid_count": avoid_count,
            "clear_count": clear_count,
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ui-api/routes/block_demo")
def ui_routes_block_demo(count: int = 3) -> dict[str, Any]:
    try:
        conn = _conn()
        route_limit = max(1, min(int(count), 10))
        routes = _route_segments(conn)
        candidates = [route for route in routes if not route.get("is_blocked")][:route_limit]

        updates: list[dict[str, Any]] = []
        for idx, route in enumerate(candidates, start=1):
            reason = f"Demo blockage {idx}: structural debris collapse"
            edge_attrs = {
                "route_id": route.get("route_id"),
                "distance_km": route.get("distance_km"),
                "estimated_time_min": route.get("estimated_time_min"),
                "is_blocked": True,
                "status": "closed",
                "blockage_reason": reason,
            }
            conn.upsertEdge(
                "Zone",
                str(route.get("from_zone_id") or ""),
                "connects",
                "Zone",
                str(route.get("to_zone_id") or ""),
                edge_attrs,
            )

            route_id = str(route.get("route_id") or "")
            if route_id:
                try:
                    conn.upsertVertex(
                        "Route",
                        route_id,
                        {
                            "is_blocked": True,
                            "blockage_reason": reason,
                        },
                    )
                except Exception:
                    pass

            updates.append(
                {
                    "route_id": route.get("route_id"),
                    "from_zone_id": route.get("from_zone_id"),
                    "to_zone_id": route.get("to_zone_id"),
                    "blockage_reason": reason,
                }
            )

        _invalidate_routes_cache()
        return {
            "status": "ok",
            "requested": route_limit,
            "updated_count": len(updates),
            "updated": updates,
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/ui-api/health")
@app.get("/ui-api/health/status")
def ui_health_status() -> dict[str, Any]:
    ingestion_health = _dispatch_ingest_health()
    dashboard_health = {"status": "ok"}
    return {
        "dashboard": dashboard_health,
        "ingestion": ingestion_health,
    }


@app.post("/ui-api/ingest/firms")
def ui_ingest_firms() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("firms")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/usgs")
def ui_ingest_usgs() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("usgs")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/eonet")
def ui_ingest_eonet() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("eonet")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/gdacs")
def ui_ingest_gdacs() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("gdacs")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/open_meteo")
def ui_ingest_open_meteo() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("open_meteo")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/air_quality")
def ui_ingest_air_quality() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("air_quality")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/osm_resources")
def ui_ingest_osm_resources() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("osm_resources")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/prune")
def ui_ingest_prune() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("prune")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/sentinel")
def ui_ingest_sentinel() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("sentinel")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/osm_roads")
def ui_ingest_osm_roads() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("osm_roads")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/run_all")
def ui_ingest_run_all() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("run_all")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/scheduler/trigger")
def ui_ingest_scheduler_trigger() -> dict[str, Any]:
    try:
        result = _dispatch_ingest("scheduler/trigger")
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/agent/run_once")
def ui_agent_run_once() -> dict[str, Any]:
    try:
        result = run_detection_cycle_once()
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        LOGGER.exception("Agent run once failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/map/live", response_class=HTMLResponse)
def map_live() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DisasterGraph Live Risk Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    :root {
      --bg: #0f172a;
      --panel: #111827;
      --accent: #22c55e;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --danger: #ef4444;
      --warn: #f59e0b;
      --safe: #10b981;
    }
    html, body {
      margin: 0;
      height: 100%;
      font-family: Segoe UI, Tahoma, Geneva, Verdana, sans-serif;
      color: var(--text);
      background: radial-gradient(circle at top, #1f2937, #020617);
    }
    .layout {
      display: grid;
      grid-template-columns: 340px 1fr;
      height: 100%;
    }
    .panel {
      background: linear-gradient(180deg, #111827 0%, #0b1220 100%);
      border-right: 1px solid #1f2937;
      padding: 16px;
      overflow: auto;
    }
    .title {
      font-size: 20px;
      margin: 0 0 8px;
      color: #f8fafc;
    }
    .subtitle {
      margin: 0 0 16px;
      color: var(--muted);
      font-size: 13px;
    }
    .stat {
      background: rgba(30, 41, 59, 0.6);
      border: 1px solid #334155;
      border-radius: 10px;
      padding: 10px 12px;
      margin-bottom: 10px;
    }
    .stat .k {
      color: var(--muted);
      font-size: 12px;
    }
    .stat .v {
      font-size: 22px;
      font-weight: 700;
      margin-top: 4px;
    }
    .section {
      margin-top: 16px;
      font-size: 13px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .event {
      margin-top: 8px;
      padding: 10px;
      border: 1px solid #374151;
      border-radius: 8px;
      background: rgba(17, 24, 39, 0.85);
      font-size: 13px;
      line-height: 1.3;
    }
    .event .sev {
      font-weight: 700;
      color: #fde68a;
    }
    #map {
      width: 100%;
      height: 100%;
    }
    .legend {
      position: absolute;
      right: 16px;
      bottom: 16px;
      background: rgba(15, 23, 42, 0.92);
      border: 1px solid #334155;
      border-radius: 8px;
      padding: 10px;
      font-size: 12px;
      color: #e2e8f0;
      z-index: 1000;
    }
    .dot {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      margin-right: 8px;
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="panel">
      <h1 class="title">DisasterGraph Live Risk Map</h1>
      <p class="subtitle">Auto-refresh every 60s. Zones are colored by severity and affected status.</p>
      <div class="stat"><div class="k">Total Zones</div><div id="zoneCount" class="v">-</div></div>
      <div class="stat"><div class="k">Affected Zones</div><div id="affectedCount" class="v">-</div></div>
      <div class="stat"><div class="k">Active Events</div><div id="eventCount" class="v">-</div></div>
      <div class="section">Top Active Events</div>
      <div id="events"></div>
    </aside>
    <main style="position: relative;">
      <div id="map"></div>
      <div class="legend">
        <div><span class="dot" style="background:#dc2626;"></span>High severity (>= 0.7)</div>
        <div><span class="dot" style="background:#ea580c;"></span>Medium severity (>= 0.4)</div>
        <div><span class="dot" style="background:#d97706;"></span>Low severity (< 0.4)</div>
        <div><span class="dot" style="background:#64748b;"></span>Not affected</div>
      </div>
    </main>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map('map').setView([28.6139, 77.2090], 10);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);

    let zoneLayer = L.layerGroup().addTo(map);
    let eventLayer = L.layerGroup().addTo(map);
    let hasFitBounds = false;

    function zoneColor(zone) {
      if (!zone.is_affected) return '#64748b';
      if (zone.severity >= 0.7) return '#dc2626';
      if (zone.severity >= 0.4) return '#ea580c';
      return '#d97706';
    }

    function zoneRadius(zone) {
      const base = zone.is_affected ? 7 : 4;
      return base + Math.round((zone.severity || 0) * 12);
    }

    async function refreshMap() {
      const resp = await fetch('/map/data', { cache: 'no-store' });
      if (!resp.ok) return;
      const data = await resp.json();

      document.getElementById('zoneCount').textContent = data.meta.zone_count;
      document.getElementById('affectedCount').textContent = data.meta.affected_zone_count;
      document.getElementById('eventCount').textContent = data.meta.active_event_count;

      const zonesById = {};
      const points = [];
      zoneLayer.clearLayers();
      eventLayer.clearLayers();

      const zoneEvents = {};
      for (const ev of data.active_events) {
        for (const zid of (ev.affected_zone_ids || [])) {
          if (!zoneEvents[zid]) zoneEvents[zid] = [];
          zoneEvents[zid].push(ev);
        }
      }

      for (const zone of data.zones) {
        zonesById[zone.id] = zone;
        const evList = zoneEvents[zone.id] || [];
        let hazardText = '';
        if (evList.length > 0) {
          hazardText = '<br><b>Hazard:</b> <span style="color:#ef4444;font-weight:700;">' + evList.map(e => (e.event_type || 'hazard').toUpperCase() + ' (' + (e.source || e.satellite_source || 'Live') + ')').join(', ') + '</span>';
        } else if (zone.is_affected) {
          hazardText = '<br><b>Hazard:</b> <span style="color:#f59e0b;font-weight:600;">AFFECTED</span>';
        } else {
          hazardText = '<br><b>Status:</b> <span style="color:#10b981;">Safe / Clear</span>';
        }

        const marker = L.circleMarker([zone.lat, zone.lng], {
          radius: zoneRadius(zone),
          color: zoneColor(zone),
          weight: 2,
          fillColor: zoneColor(zone),
          fillOpacity: zone.is_affected ? 0.35 : 0.2
        }).bindPopup(
          '<b>' + zone.name + '</b><br>' +
          '<b>ID:</b> ' + zone.id +
          hazardText + '<br>' +
          '<b>Severity:</b> ' + Number(zone.severity || 0).toFixed(3)
        );
        marker.addTo(zoneLayer);
        points.push([zone.lat, zone.lng]);
      }

      const topEvents = data.active_events.slice(0, 8);
      const eventsEl = document.getElementById('events');
      eventsEl.innerHTML = '';
      for (const event of topEvents) {
        const srcText = event.source || event.satellite_source || 'Live';
        const card = document.createElement('div');
        card.className = 'event';
        card.innerHTML =
          '<div><b>' + (event.event_type || 'hazard').toUpperCase() + '</b> <span class="sev">sev ' + Number(event.severity || 0).toFixed(3) + '</span></div>' +
          '<div style="color:#38bdf8;font-size:11px;font-weight:600;">Source: ' + srcText + '</div>' +
          '<div style="color:#94a3b8;font-size:11px;">' + event.event_id + '</div>' +
          (event.timestamp ? '<div style="color:#cbd5e1;font-size:11px;">Time: ' + event.timestamp + '</div>' : '') +
          '<div style="color:#94a3b8;font-size:11px;">Zones affected: ' + (event.affected_zone_ids || []).length + '</div>';
        eventsEl.appendChild(card);

        const zoneIds = event.affected_zone_ids || [];
        if (zoneIds.length > 0) {
          const firstZone = zonesById[zoneIds[0]];
          if (firstZone) {
            const pulse = L.circle([firstZone.lat, firstZone.lng], {
              radius: 1200 + ((event.severity || 0) * 4500),
              color: '#ef4444',
              weight: 1,
              fillColor: '#ef4444',
              fillOpacity: 0.1
            }).bindPopup(
              '<b>' + (event.event_type || 'hazard').toUpperCase() + '</b><br>' +
              '<b>Source:</b> ' + srcText + '<br>' +
              '<b>Event ID:</b> ' + event.event_id +
              (event.timestamp ? '<br><b>Time:</b> ' + event.timestamp : '')
            );
            pulse.addTo(eventLayer);
          }
        }
      }

      if (!hasFitBounds && points.length > 1) {
        map.fitBounds(points, { padding: [30, 30] });
        hasFitBounds = true;
      }
    }

    refreshMap();
    setInterval(refreshMap, 60000);
  </script>
</body>
</html>
"""


_assignment_status_overrides: dict[str, str] = {}


def _update_neo4j_assignment_status(assignment_id: str, new_status: str) -> None:
    try:
        parts = assignment_id.split("::")
        if len(parts) != 2:
            parts = assignment_id.split("_", 1)
        if len(parts) == 2:
            res_id, person_id = parts
            conn = _conn()
            if hasattr(conn, "run_query"):
                conn.run_query(
                    "MATCH (r:Resource)-[a:ASSIGNED_TO]->(p:Person) "
                    "WHERE (r.res_id = $res_id OR r.id = $res_id) AND p.id = $person_id "
                    "SET a.status = $status RETURN a",
                    {"res_id": res_id, "person_id": person_id, "status": new_status},
                )
    except Exception as exc:
        LOGGER.warning("Could not update Neo4j assignment status: %s", exc)


@app.post("/ui-api/assignments/{assignment_id:path}/approve")
def ui_assignment_approve(assignment_id: str) -> dict[str, Any]:
    _assignment_status_overrides[assignment_id] = "dispatched"
    _update_neo4j_assignment_status(assignment_id, "dispatched")
    _invalidate_overview_cache()
    return {"status": "ok", "assignment_id": assignment_id, "new_status": "dispatched"}


@app.post("/ui-api/assignments/{assignment_id:path}/reject")
def ui_assignment_reject(assignment_id: str) -> dict[str, Any]:
    _assignment_status_overrides[assignment_id] = "rejected"
    _update_neo4j_assignment_status(assignment_id, "rejected")
    _invalidate_overview_cache()
    return {"status": "ok", "assignment_id": assignment_id, "new_status": "rejected"}


@app.get("/ui-api/route/directions")
def ui_route_directions(
    from_lat: float, from_lng: float, to_lat: float, to_lng: float
) -> dict[str, Any]:
    try:
        router = RoadRouter.get_instance()
        route = router.compute_route(from_lat, from_lng, to_lat, to_lng)
        return {
            "origin": {"lat": from_lat, "lng": from_lng},
            "destination": {"lat": to_lat, "lng": to_lng},
            "distance_km": route.distance_km,
            "eta_min": route.eta_min,
            "fastest_eta_min": route.fastest_eta_min,
            "extra_minutes": route.extra_minutes,
            "uses_hazard_road": route.uses_hazard_road,
            "no_safe_route": route.no_safe_route,
            "routing_source": route.routing_source,
            "encoded_geometry": route.encoded_geometry,
            "route_coords": [[pt[0], pt[1]] for pt in route.coords],
            "fastest_coords": [[pt[0], pt[1]] for pt in route.fastest_coords],
            "hazard_zones_crossed": route.hazard_zones_crossed,
            "road_names": route.road_names,
            "status_summary": route.status_summary,
            "reason": route.reason,
            "segments": route.segments[:25],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/assignments/live")
def assignments_live() -> dict[str, Any]:
    try:
        conn = _conn()
        assignments: list[dict[str, Any]] = []

        if hasattr(conn, "run_query"):
            try:
                cypher = (
                    "MATCH (r:Resource)-[a:ASSIGNED_TO]->(p:Person) "
                    "OPTIONAL MATCH (p)-[:LOCATED_IN]->(z:Zone) "
                    "RETURN properties(r) AS r, properties(a) AS a, properties(p) AS p, "
                    "z.name AS zone_name, z.zone_id AS zone_id, z.disaster_severity AS zone_sev"
                )
                rows = conn.run_query(cypher)
                for row in rows:
                    r_props = row.get("r") or {}
                    a_props = row.get("a") or {}
                    p_props = row.get("p") or {}

                    res_id = str(r_props.get("res_id") or r_props.get("id") or "")
                    person_id = str(p_props.get("id") or "")
                    if not res_id or not person_id:
                        continue

                    assignment_key = f"{res_id}::{person_id}"
                    status = _assignment_status_overrides.get(assignment_key) or a_props.get("status") or "proposed"
                    eta = float(a_props.get("eta_min") or 15.0)

                    res_type = str(r_props.get("resource_type") or "ambulance").lower()
                    clean_res_num = res_id.replace("res_", "").replace("RES_", "").lstrip("0") or "1"
                    res_name = f"{res_type.capitalize()} {clean_res_num}"

                    p_name = p_props.get("name") or f"Resident {person_id.replace('person_', '').lstrip('0')}"
                    z_name = row.get("zone_name") or f"Zone {row.get('zone_id', '')}"
                    medical = p_props.get("medical_needs") or "urgent medical care"
                    zone_sev = float(row.get("zone_sev") or 0.7)

                    res_lat = _to_float(r_props.get("location_lat"))
                    res_lng = _to_float(r_props.get("location_lng"))
                    person_lat = _to_float(p_props.get("location_lat"))
                    person_lng = _to_float(p_props.get("location_lng"))

                    encoded_geom = str(a_props.get("encoded_geometry") or "").strip()
                    route_coords: list[tuple[float, float]] = []
                    fastest_coords: list[tuple[float, float]] = []
                    distance_km = _to_float(a_props.get("distance_km"))
                    fastest_eta = _to_float(a_props.get("fastest_eta_min"))
                    extra_minutes = _to_float(a_props.get("extra_minutes"))
                    routing_source = str(a_props.get("routing_source") or "osmnx")
                    no_safe_route = _to_bool(a_props.get("no_safe_route", False))
                    uses_hazard = _to_bool(a_props.get("uses_hazard_road", False))
                    hazard_zones_crossed: list[str] = []
                    road_names: list[str] = []

                    # Decode encoded geometry if present
                    if encoded_geom:
                        try:
                            route_coords = decode_polyline(encoded_geom)
                        except Exception:
                            route_coords = []

                    # If not present or empty, dynamically compute using RoadRouter
                    if (not route_coords or len(route_coords) < 2) and res_lat is not None and res_lng is not None and person_lat is not None and person_lng is not None:
                        try:
                            router = RoadRouter.get_instance()
                            computed = router.compute_route(res_lat, res_lng, person_lat, person_lng)
                            route_coords = computed.coords
                            fastest_coords = computed.fastest_coords
                            encoded_geom = computed.encoded_geometry
                            distance_km = computed.distance_km
                            eta = computed.eta_min
                            fastest_eta = computed.fastest_eta_min
                            extra_minutes = computed.extra_minutes
                            uses_hazard = computed.uses_hazard_road
                            no_safe_route = computed.no_safe_route
                            routing_source = computed.routing_source
                            hazard_zones_crossed = computed.hazard_zones_crossed
                            road_names = computed.road_names
                        except Exception as r_exc:
                            LOGGER.debug("Dynamic routing computation for assignment %s failed: %s", assignment_key, r_exc)

                    if not route_coords and res_lat is not None and res_lng is not None and person_lat is not None and person_lng is not None:
                        route_coords = [(res_lat, res_lng), (person_lat, person_lng)]
                        routing_source = "approximate"

                    if not fastest_coords and route_coords:
                        fastest_coords = route_coords

                    if no_safe_route:
                        ai_reason = f"No safe route available: all corridors to {z_name} are blocked by hazardous events."
                        route_status = "no_safe_route"
                    elif uses_hazard or (extra_minutes and extra_minutes > 0):
                        ai_reason = f"Hazard-aware dispatch ({eta:.1f}m ETA, +{extra_minutes or 0:.1f}m detour); avoids toxic plume in {z_name}."
                        route_status = "uses_hazard_road"
                    else:
                        ai_reason = f"Direct clear route ({eta:.1f}m ETA, {distance_km or 5.0:.1f}km); corridor clear to reach resident ({medical})."
                        route_status = "clear"

                    is_test = res_id.lower().startswith("e2e") or person_id.lower().startswith("e2e")

                    assignments.append(
                        {
                            "assignment_id": assignment_key,
                            "resource_id": res_id,
                            "resource_name": res_name,
                            "resource_type": res_type,
                            "resource_lat": res_lat,
                            "resource_lng": res_lng,
                            "person_id": person_id,
                            "person_name": p_name,
                            "person_zone": z_name,
                            "person_lat": person_lat,
                            "person_lng": person_lng,
                            "medical_needs": medical,
                            "assigned_at": a_props.get("assigned_at"),
                            "eta_min": eta,
                            "fastest_eta_min": fastest_eta if fastest_eta is not None else eta,
                            "extra_minutes": extra_minutes if extra_minutes is not None else 0.0,
                            "distance_km": distance_km if distance_km is not None else 5.0,
                            "status": status,
                            "route_status": route_status,
                            "no_safe_route": no_safe_route,
                            "uses_hazard_road": uses_hazard,
                            "routing_source": routing_source,
                            "encoded_geometry": encoded_geom,
                            "route_coords": [[pt[0], pt[1]] for pt in route_coords],
                            "fastest_coords": [[pt[0], pt[1]] for pt in fastest_coords],
                            "hazard_zones_crossed": hazard_zones_crossed,
                            "road_names": road_names,
                            "ai_reason": ai_reason,
                            "is_test_data": is_test,
                        }
                    )
            except Exception as exc:
                LOGGER.warning("run_query in assignments_live failed, falling back: %s", exc)

        if not assignments:
            resources = conn.getVertices("Resource", limit=10000)
            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                res_id = str(resource.get("v_id") or resource.get("id") or "")
                if not res_id:
                    continue

                edges = conn.getEdges("Resource", res_id, "assigned_to")
                for edge in edges:
                    if not isinstance(edge, dict):
                        continue
                    attrs = edge.get("attributes", {}) if "attributes" in edge else {}
                    person_id = str(edge.get("to_id") or "")
                    assignment_key = f"{res_id}::{person_id}"
                    status = _assignment_status_overrides.get(assignment_key) or attrs.get("status") or "proposed"
                    eta = float(attrs.get("eta_min") or 15.0)
                    is_test = res_id.lower().startswith("e2e") or person_id.lower().startswith("e2e")

                    encoded_geom = str(attrs.get("encoded_geometry") or "").strip()
                    route_coords: list[tuple[float, float]] = []
                    if encoded_geom:
                        try:
                            route_coords = decode_polyline(encoded_geom)
                        except Exception:
                            pass

                    assignments.append(
                        {
                            "assignment_id": assignment_key,
                            "resource_id": res_id,
                            "resource_name": f"Ambulance {res_id.replace('res_', '').lstrip('0') or '1'}",
                            "resource_type": "ambulance",
                            "person_id": person_id,
                            "person_name": f"Resident {person_id.replace('person_', '').lstrip('0')}",
                            "person_zone": "Central Ward",
                            "medical_needs": "urgent care",
                            "assigned_at": attrs.get("assigned_at"),
                            "eta_min": eta,
                            "fastest_eta_min": float(attrs.get("fastest_eta_min") or eta),
                            "extra_minutes": float(attrs.get("extra_minutes") or 0.0),
                            "distance_km": float(attrs.get("distance_km") or 5.0),
                            "status": status,
                            "route_status": "clear",
                            "no_safe_route": False,
                            "uses_hazard_road": False,
                            "routing_source": str(attrs.get("routing_source") or "approximate"),
                            "encoded_geometry": encoded_geom,
                            "route_coords": [[pt[0], pt[1]] for pt in route_coords],
                            "fastest_coords": [[pt[0], pt[1]] for pt in route_coords],
                            "hazard_zones_crossed": [],
                            "road_names": [],
                            "ai_reason": f"Direct dispatch ({eta:.1f}m ETA); avoiding hazard corridors.",
                            "is_test_data": is_test,
                        }
                    )

        assignments.sort(key=lambda x: str(x.get("assigned_at", "")), reverse=True)
        return {"assignments": assignments, "count": len(assignments)}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
