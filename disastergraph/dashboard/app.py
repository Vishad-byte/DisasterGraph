from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from disastergraph.config import get_settings, get_tg_connection


app = FastAPI(title="DisasterGraph Dashboard API", version="0.1.0")
settings = get_settings()


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
