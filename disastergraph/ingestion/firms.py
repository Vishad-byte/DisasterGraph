from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from typing import Any

import httpx

from disastergraph.graph.utils import list_zones, nearest_zone

LOGGER = logging.getLogger(__name__)

INDIA_BBOX = (68.7, 6.5, 97.25, 37.6)


@dataclass(slots=True)
class FirmsPoint:
    latitude: float
    longitude: float
    brightness: float
    acq_date: str
    acq_time: str
    frp: float = 0.0
    confidence: str = ""


def parse_firms_csv(csv_text: str) -> list[FirmsPoint]:
    reader = csv.DictReader(StringIO(csv_text))
    points: list[FirmsPoint] = []
    for row in reader:
        try:
            lat = float(row.get("latitude", 0.0))
            lng = float(row.get("longitude", 0.0))
            b = float(row.get("bright_ti4", row.get("brightness", 0.0)))
            frp_val = float(row.get("frp", 0.0) or 0.0)
            conf_val = str(row.get("confidence", "") or "")
            points.append(
                FirmsPoint(
                    latitude=lat,
                    longitude=lng,
                    brightness=b,
                    acq_date=str(row.get("acq_date", "") or ""),
                    acq_time=str(row.get("acq_time", "") or ""),
                    frp=frp_val,
                    confidence=conf_val,
                )
            )
        except (TypeError, ValueError):
            continue
    return points


def _inside_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    # If bbox is given as (west, south, east, north) where west > south
    b1, b2, b3, b4 = bbox
    if b1 > b2 and b3 > b4:
        west, south, east, north = b1, b2, b3, b4
    else:
        # Standard DisasterGraph format: (south, west, north, east)
        south, west, north, east = b1, b2, b3, b4
    return south <= lat <= north and west <= lng <= east


def fetch_firms_csv(
    map_key: str,
    bbox: tuple[float, float, float, float] = INDIA_BBOX,
    timeout: float = 30.0,
    retries: int = 2,
) -> str:
    """Fetch real-time active fire hotspots from NASA FIRMS API."""
    bbox_text = ",".join(str(x) for x in bbox)
    url = (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{map_key}/VIIRS_SNPP_NRT/{bbox_text}/1"
    )
    last_err: Exception | None = None
    for attempt in range(max(1, retries + 1)):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url)
                response.raise_for_status()
                return response.text
        except Exception as exc:
            last_err = exc
            LOGGER.warning("FIRMS fetch attempt %d failed: %s", attempt + 1, exc)
    if last_err is not None:
        raise last_err
    return ""


def ingest_firms_from_csv(
    conn: Any,
    csv_text: str,
    source: str = "FIRMS",
    bbox: tuple[float, float, float, float] = INDIA_BBOX,
) -> dict[str, int]:
    points = parse_firms_csv(csv_text)
    zones = list_zones(conn)
    writes = 0
    events = 0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for idx, point in enumerate(points):
        if not _inside_bbox(point.latitude, point.longitude, bbox):
            continue

        if point.brightness <= 300.0:
            continue

        zone = nearest_zone(point.latitude, point.longitude, zones)
        # Stable external ID based on coordinates and acquisition timestamp
        date_part = point.acq_date or "live"
        time_part = point.acq_time or str(idx)
        event_id = f"firms_{date_part}_{time_part}_{point.latitude:.4f}_{point.longitude:.4f}"
        severity = round(min(1.0, point.brightness / 500.0), 3)

        ts_str = (
            f"{point.acq_date} {point.acq_time[:2]}:{point.acq_time[2:]}:00"
            if len(point.acq_time) >= 4 and point.acq_date
            else now_utc
        )

        writes += conn.upsertVertex(
            "DisasterEvent",
            event_id,
            {
                "event_type": "fire",
                "type": "fire",
                "timestamp": ts_str,
                "ts": ts_str,
                "severity": severity,
                "frp": point.frp,
                "confidence": point.confidence,
                "lat": point.latitude,
                "lon": point.longitude,
                "latitude": point.latitude,
                "longitude": point.longitude,
                "satellite_source": source,
                "source": source,
                "status": "active",
                "ingested_at": now_utc,
            },
        )
        writes += conn.upsertEdge(
            "DisasterEvent",
            event_id,
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
