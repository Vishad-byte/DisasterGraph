from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from disastergraph.graph.utils import list_zones, nearest_zone

LOGGER = logging.getLogger(__name__)

GDACS_API_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"

TYPE_MAP = {
    "FL": "flood",
    "TC": "cyclone",
    "EQ": "earthquake",
    "VO": "volcano",
    "DR": "drought",
    "WF": "fire",
}

SEVERITY_BY_ALERT = {
    "red": 0.95,
    "orange": 0.75,
    "green": 0.45,
}


@dataclass(slots=True)
class GdacsAlert:
    event_id: str
    event_type: str
    title: str
    alert_level: str
    severity: float
    latitude: float
    longitude: float
    from_date: str


def _inside_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lng <= east


def parse_gdacs_geojson(
    data: dict[str, Any],
    bbox: tuple[float, float, float, float] | None = None,
) -> list[GdacsAlert]:
    features = data.get("features", []) if isinstance(data, dict) else []
    results: list[GdacsAlert] = []

    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties", {}) or {}
        geom = feat.get("geometry", {}) or {}
        coords = geom.get("coordinates", [])

        if len(coords) < 2:
            continue

        lng, lat = float(coords[0]), float(coords[1])
        if bbox is not None and not _inside_bbox(lat, lng, bbox):
            continue

        raw_type = str(props.get("eventtype") or "DIS")
        event_type = TYPE_MAP.get(raw_type.upper(), "disaster")
        raw_id = str(props.get("eventid") or f"{lat}_{lng}")
        alert_level = str(props.get("alertlevel") or "green").lower()
        severity = SEVERITY_BY_ALERT.get(alert_level, 0.6)
        title = str(props.get("name") or props.get("eventname") or "GDACS Alert")
        from_date = str(props.get("fromdate") or "")

        results.append(
            GdacsAlert(
                event_id=f"gdacs_{raw_type.lower()}_{raw_id}",
                event_type=event_type,
                title=title,
                alert_level=alert_level,
                severity=severity,
                latitude=lat,
                longitude=lng,
                from_date=from_date,
            )
        )
    return results


def fetch_gdacs_alerts(
    event_list: str = "EQ;TC;FL;VO;WF",
    limit: int = 50,
    timeout: float = 20.0,
    retries: int = 2,
) -> dict[str, Any]:
    params = {"eventlist": event_list, "limit": limit}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            with httpx.Client(timeout=timeout, headers=headers) as client:
                resp = client.get(GDACS_API_URL, params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            last_err = exc
            LOGGER.warning("GDACS fetch attempt %d failed: %s", attempt + 1, exc)
    if last_err is not None:
        raise last_err
    return {}


def ingest_gdacs_data(
    conn: Any,
    data: dict[str, Any],
    bbox: tuple[float, float, float, float] | None = None,
    source: str = "GDACS",
) -> dict[str, int]:
    alerts = parse_gdacs_geojson(data, bbox=bbox)
    zones = list_zones(conn)
    if not zones:
        return {"events": 0, "writes": 0}

    writes = 0
    events_count = 0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for al in alerts:
        zone = nearest_zone(al.latitude, al.longitude, zones)
        writes += conn.upsertVertex(
            "DisasterEvent",
            al.event_id,
            {
                "event_type": al.event_type,
                "type": al.event_type,
                "title": al.title,
                "alert_level": al.alert_level,
                "timestamp": al.from_date or now_utc,
                "ts": al.from_date or now_utc,
                "severity": al.severity,
                "lat": al.latitude,
                "lon": al.longitude,
                "latitude": al.latitude,
                "longitude": al.longitude,
                "satellite_source": source,
                "source": source,
                "status": "active",
                "ingested_at": now_utc,
            },
        )
        writes += conn.upsertEdge(
            "DisasterEvent",
            al.event_id,
            "affects",
            "Zone",
            zone.zone_id,
            {"severity": al.severity},
        )
        writes += conn.upsertVertex(
            "Zone",
            zone.zone_id,
            {"is_affected": True, "disaster_severity": al.severity},
        )
        events_count += 1

    return {"events": events_count, "writes": writes}
