from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from disastergraph.graph.utils import list_zones, nearest_zone

LOGGER = logging.getLogger(__name__)

USGS_ALL_DAY_FEED = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"


@dataclass(slots=True)
class UsgsEarthquake:
    event_id: str
    latitude: float
    longitude: float
    depth_km: float
    magnitude: float
    place: str
    time_iso: str


def _inside_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    south, west, north, east = bbox
    return south <= lat <= north and west <= lng <= east


def parse_usgs_geojson(
    data: dict[str, Any],
    bbox: tuple[float, float, float, float] | None = None,
    min_magnitude: float = 2.0,
) -> list[UsgsEarthquake]:
    features = data.get("features", []) if isinstance(data, dict) else []
    results: list[UsgsEarthquake] = []

    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties", {}) or {}
        geom = feat.get("geometry", {}) or {}
        coords = geom.get("coordinates", [])

        if len(coords) < 2:
            continue

        lng, lat = float(coords[0]), float(coords[1])
        depth = float(coords[2]) if len(coords) >= 3 else 0.0
        mag = float(props.get("mag") or 0.0)

        if mag < min_magnitude:
            continue

        if bbox is not None and not _inside_bbox(lat, lng, bbox):
            continue

        feat_id = str(feat.get("id") or props.get("code") or f"{lat}_{lng}")
        epoch_ms = props.get("time")
        if epoch_ms:
            ts_str = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        else:
            ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        results.append(
            UsgsEarthquake(
                event_id=f"usgs_{feat_id}",
                latitude=lat,
                longitude=lng,
                depth_km=depth,
                magnitude=mag,
                place=str(props.get("place") or ""),
                time_iso=ts_str,
            )
        )
    return results


def fetch_usgs_feed(
    feed_url: str = USGS_ALL_DAY_FEED,
    timeout: float = 20.0,
    retries: int = 2,
) -> dict[str, Any]:
    last_err: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(feed_url)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            last_err = exc
            LOGGER.warning("USGS fetch attempt %d failed: %s", attempt + 1, exc)
    if last_err is not None:
        raise last_err
    return {}


def ingest_usgs_data(
    conn: Any,
    geojson_data: dict[str, Any],
    bbox: tuple[float, float, float, float] | None = None,
    min_magnitude: float = 2.0,
    source: str = "USGS",
) -> dict[str, int]:
    quakes = parse_usgs_geojson(geojson_data, bbox=bbox, min_magnitude=min_magnitude)
    zones = list_zones(conn)
    if not zones:
        return {"events": 0, "writes": 0}

    writes = 0
    events = 0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for q in quakes:
        zone = nearest_zone(q.latitude, q.longitude, zones)
        severity = round(min(1.0, max(0.2, (q.magnitude - 2.0) / 5.0)), 3)

        writes += conn.upsertVertex(
            "DisasterEvent",
            q.event_id,
            {
                "event_type": "earthquake",
                "type": "earthquake",
                "timestamp": q.time_iso,
                "ts": q.time_iso,
                "severity": severity,
                "magnitude": q.magnitude,
                "depth_km": q.depth_km,
                "place": q.place,
                "lat": q.latitude,
                "lon": q.longitude,
                "latitude": q.latitude,
                "longitude": q.longitude,
                "satellite_source": source,
                "source": source,
                "status": "active",
                "ingested_at": now_utc,
            },
        )
        writes += conn.upsertEdge(
            "DisasterEvent",
            q.event_id,
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
        events += 1

    return {"events": events, "writes": writes}
