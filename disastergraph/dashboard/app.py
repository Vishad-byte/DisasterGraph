from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from disastergraph.config import get_settings, get_tg_connection
from disastergraph.agent.runner import run_detection_cycle_once


app = FastAPI(title="DisasterGraph Dashboard API", version="0.1.0")
settings = get_settings()
LOGGER = logging.getLogger(__name__)
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


def _http_post_json(url: str, timeout_seconds: float = 120.0) -> dict[str, Any]:
    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(url)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=502, detail=f"Invalid response from upstream POST {url}")
        return payload


def _conn() -> Any:
    return get_tg_connection(settings)


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
        rows.append(
            {
                "event_id": event_id,
                "event_type": str(attrs.get("event_type", "unknown")),
                "severity": float(attrs.get("severity", 0) or 0),
                "timestamp": attrs.get("timestamp"),
                "satellite_source": attrs.get("satellite_source"),
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

            segments.append(
                {
                    "route_id": route_id,
                    "from_zone_id": from_zone_id,
                    "to_zone_id": to_zone_id,
                    "from": {
                        "lat": from_zone["lat"],
                        "lng": from_zone["lng"],
                    },
                    "to": {
                        "lat": to_zone["lat"],
                        "lng": to_zone["lng"],
                    },
                    "distance_km": _to_float(attrs.get("distance_km")),
                    "estimated_time_min": _to_float(attrs.get("estimated_time_min")),
                    "is_blocked": _to_bool(attrs.get("is_blocked", False)),
                    "blockage_reason": str(attrs.get("blockage_reason") or ""),
                }
            )

    return segments


def _overview_payload(conn: Any, include_assignments: bool = False) -> dict[str, Any]:
    zones = _zone_points(conn)
    active_events = _active_events_with_affected_zones(conn)

    resources_raw = _as_rows(conn.getVertices("Resource", limit=10000))
    available_resources = 0
    resources_by_type: dict[str, int] = {}
    for resource in resources_raw:
        is_available = _to_bool(resource.get("is_available", False))
        if is_available:
            available_resources += 1
        r_type = str(resource.get("resource_type", "unknown") or "unknown")
        resources_by_type[r_type] = resources_by_type.get(r_type, 0) + 1

    assignments: list[dict[str, Any]] = []
    if include_assignments:
        for resource in resources_raw:
            res_id = str(resource.get("id") or "")
            if not res_id:
                continue
            edges = conn.getEdges("Resource", res_id, "assigned_to")
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                attrs = edge.get("attributes", {}) if "attributes" in edge else {}
                assignments.append(
                    {
                        "resource_id": res_id,
                        "person_id": edge.get("to_id"),
                        "assigned_at": attrs.get("assigned_at"),
                        "eta_min": attrs.get("eta_min"),
                    }
                )
        assignments.sort(key=lambda x: str(x.get("assigned_at", "")), reverse=True)

    affected_zone_count = len([z for z in zones if z.get("is_affected")])

    return {
        "zones": zones,
        "active_events": active_events,
        "assignments": assignments,
        "meta": {
            "zone_count": len(zones),
            "affected_zone_count": affected_zone_count,
            "active_event_count": len(active_events),
            "assignment_count": len(assignments),
            "resource_count": len(resources_raw),
            "available_resource_count": available_resources,
            "resources_by_type": resources_by_type,
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
        blocked_count = len([route for route in routes if route.get("is_blocked")])
        return {
            "routes": routes,
            "count": len(routes),
            "blocked_count": blocked_count,
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
            reason = f"Demo blockage {idx}"
            edge_attrs = {
                "route_id": route.get("route_id"),
                "distance_km": route.get("distance_km"),
                "estimated_time_min": route.get("estimated_time_min"),
                "is_blocked": True,
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
def ui_health() -> dict[str, Any]:
    ingestion_health: dict[str, Any] = {
        "status": "unknown",
        "detail": "not_checked",
    }
    try:
        ingestion_health = _http_get_json(_ingestion_url("/ingest/health"))
    except Exception as exc:  # pragma: no cover
        ingestion_health = {
            "status": "down",
            "detail": str(exc),
        }

    dashboard_health = {"status": "ok"}
    return {
        "dashboard": dashboard_health,
        "ingestion": ingestion_health,
    }


@app.post("/ui-api/ingest/firms")
def ui_ingest_firms() -> dict[str, Any]:
    try:
        result = _http_post_json(_ingestion_url("/ingest/firms"))
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/sentinel")
def ui_ingest_sentinel() -> dict[str, Any]:
    try:
        result = _http_post_json(_ingestion_url("/ingest/sentinel"))
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/osm_roads")
def ui_ingest_osm_roads() -> dict[str, Any]:
    try:
        result = _http_post_json(_ingestion_url("/ingest/osm_roads"))
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/run_all")
def ui_ingest_run_all() -> dict[str, Any]:
    try:
        result = _http_post_json(_ingestion_url("/ingest/run_all"), timeout_seconds=600.0)
        _invalidate_ui_caches()
        return result
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/ui-api/ingest/scheduler/trigger")
def ui_ingest_scheduler_trigger() -> dict[str, Any]:
    try:
        result = _http_post_json(_ingestion_url("/ingest/scheduler/trigger"))
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
      <p class="subtitle">Auto-refresh every 10s. Zones are colored by severity and affected status.</p>
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

      for (const zone of data.zones) {
        zonesById[zone.id] = zone;
        const marker = L.circleMarker([zone.lat, zone.lng], {
          radius: zoneRadius(zone),
          color: zoneColor(zone),
          weight: 2,
          fillColor: zoneColor(zone),
          fillOpacity: zone.is_affected ? 0.35 : 0.2
        }).bindPopup(
          '<b>' + zone.name + '</b><br>' +
          'ID: ' + zone.id + '<br>' +
          'Affected: ' + zone.is_affected + '<br>' +
          'Severity: ' + Number(zone.severity || 0).toFixed(3)
        );
        marker.addTo(zoneLayer);
        points.push([zone.lat, zone.lng]);
      }

      const topEvents = data.active_events.slice(0, 8);
      const eventsEl = document.getElementById('events');
      eventsEl.innerHTML = '';
      for (const event of topEvents) {
        const card = document.createElement('div');
        card.className = 'event';
        card.innerHTML =
          '<div><b>' + event.event_type + '</b> <span class="sev">sev ' + Number(event.severity || 0).toFixed(3) + '</span></div>' +
          '<div style="color:#94a3b8">' + event.event_id + '</div>' +
          '<div style="color:#94a3b8">zones: ' + (event.affected_zone_ids || []).length + '</div>';
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
            }).bindPopup('<b>' + event.event_type + '</b><br>' + event.event_id);
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
    setInterval(refreshMap, 10000);
  </script>
</body>
</html>
"""


@app.get("/assignments/live")
def assignments_live() -> dict[str, Any]:
    try:
        conn = _conn()
        resources = conn.getVertices("Resource", limit=10000)
        assignments: list[dict[str, Any]] = []

        for resource in resources:
            if not isinstance(resource, dict):
                continue
            res_id = str(resource.get("v_id") or "")
            if not res_id:
                continue

            edges = conn.getEdges("Resource", res_id, "assigned_to")
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                attrs = edge.get("attributes", {}) if "attributes" in edge else {}
                assignments.append(
                    {
                        "resource_id": res_id,
                        "person_id": edge.get("to_id"),
                        "assigned_at": attrs.get("assigned_at"),
                        "eta_min": attrs.get("eta_min"),
                    }
                )

        assignments.sort(key=lambda x: str(x.get("assigned_at", "")), reverse=True)
        return {"assignments": assignments, "count": len(assignments)}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
