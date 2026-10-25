from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from disastergraph.graph.utils import list_zones, nearest_zone

LOGGER = logging.getLogger(__name__)

EONET_V3_URL = "https://eonet.gsfc.nasa.gov/api/v3/events"

CATEGORY_MAP = {
    "wildfires": "fire",
    "floods": "flood",
    "severeStorms": "storm",
    "volcanoes": "volcano",
    "earthquakes": "earthquake",
    "waterColor": "pollution",
    "landslides": "landslide",
}


@dataclass(slots=True)
class EonetEvent:
    event_id: str
    title: str
    event_type: str
    latitude: float
    longitude: float
    date_str: str


def _inside_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lng <= east


def parse_eonet_json(
    data: dict[str, Any],
    bbox: tuple[float, float, float, float] | None = None,
) -> list[EonetEvent]:
    events = data.get("events", []) if isinstance(data, dict) else []
    results: list[EonetEvent] = []

    for ev in events:
        if not isinstance(ev, dict):
            continue

        raw_id = str(ev.get("id") or "")
        title = str(ev.get("title") or "")
        categories = ev.get("categories", [])
        cat_id = categories[0].get("id", "") if categories and isinstance(categories[0], dict) else ""
        event_type = CATEGORY_MAP.get(cat_id, "disaster")

        geoms = ev.get("geometry", [])
        if not geoms or not isinstance(geoms, list):
            continue

        # Use latest recorded geometry point
        latest_geom = geoms[-1]
        if not isinstance(latest_geom, dict):
            continue

        coords = latest_geom.get("coordinates", [])
        if len(coords) < 2:
            continue

        # Coordinates in GeoJSON are [longitude, latitude]
        lng, lat = float(coords[0]), float(coords[1])
        if bbox is not None and not _inside_bbox(lat, lng, bbox):
            continue

        date_val = str(latest_geom.get("date") or "")
        results.append(
            EonetEvent(
                event_id=f"eonet_{raw_id}",
                title=title,
                event_type=event_type,
                latitude=lat,
                longitude=lng,
                date_str=date_val,
            )
        )
    return results


def fetch_eonet_events(
    bbox: tuple[float, float, float, float] | None = None,
    status: str = "open",
    limit: int = 100,
    timeout: float = 20.0,
    retries: int = 2,
) -> dict[str, Any]:
    params: dict[str, Any] = {"status": status, "limit": limit}
    if bbox is not None:
        south, west, north, east = bbox
        params["bbox"] = f"{west},{south},{east},{north}"

    last_err: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(EONET_V3_URL, params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            last_err = exc
            LOGGER.warning("EONET fetch attempt %d failed: %s", attempt + 1, exc)
    if last_err is not None:
        raise last_err
    return {}


def ingest_eonet_data(
    conn: Any,
    data: dict[str, Any],
    bbox: tuple[float, float, float, float] | None = None,
    source: str = "EONET",
) -> dict[str, int]:
    events = parse_eonet_json(data, bbox=bbox)
    zones = list_zones(conn)
    if not zones:
        return {"events": 0, "writes": 0}

    writes = 0
    events_count = 0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for ev in events:
        zone = nearest_zone(ev.latitude, ev.longitude, zones)
        severity = 0.8  # EONET major disaster events are critical

        writes += conn.upsertVertex(
            "DisasterEvent",
            ev.event_id,
            {
                "event_type": ev.event_type,
                "type": ev.event_type,
                "title": ev.title,
                "timestamp": ev.date_str or now_utc,
                "ts": ev.date_str or now_utc,
                "severity": severity,
                "lat": ev.latitude,
                "lon": ev.longitude,
                "latitude": ev.latitude,
                "longitude": ev.longitude,
                "satellite_source": source,
                "source": source,
                "status": "active",
                "ingested_at": now_utc,
            },
        )
        writes += conn.upsertEdge(
            "DisasterEvent",
            ev.event_id,
            "affects",
            "Zone",
            zone.zone_id,
            {"severity": severity},
        )
        writes += conn.upsertVertex(
            "Zone",
            zone.zone_id,
            {"is_affected": True, "disaster_severity": severity},
        )
        events_count += 1

    return {"events": events_count, "writes": writes}
