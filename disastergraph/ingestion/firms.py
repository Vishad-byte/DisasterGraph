from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from io import StringIO
from typing import Any

import httpx

from disastergraph.graph.utils import list_zones, nearest_zone


INDIA_BBOX = (68.7, 6.5, 97.25, 37.6)


@dataclass(slots=True)
class FirmsPoint:
    latitude: float
    longitude: float
    brightness: float
    acq_date: str
    acq_time: str


def parse_firms_csv(csv_text: str) -> list[FirmsPoint]:
    reader = csv.DictReader(StringIO(csv_text))
    points: list[FirmsPoint] = []
    for row in reader:
        try:
            lat = float(row.get("latitude", 0.0))
            lng = float(row.get("longitude", 0.0))
            b = float(row.get("bright_ti4", row.get("brightness", 0.0)))
            points.append(
                FirmsPoint(
                    latitude=lat,
                    longitude=lng,
                    brightness=b,
                    acq_date=row.get("acq_date", ""),
                    acq_time=row.get("acq_time", ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return points


def _inside_bbox(lat: float, lng: float, bbox: tuple[float, float, float, float]) -> bool:
    west, south, east, north = bbox
    return south <= lat <= north and west <= lng <= east


def fetch_firms_csv(map_key: str, bbox: tuple[float, float, float, float] = INDIA_BBOX) -> str:
    bbox_text = ",".join(str(x) for x in bbox)
    url = (
        "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
        f"{map_key}/VIIRS_SNPP_NRT/{bbox_text}/1"
    )
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def ingest_firms_from_csv(conn: Any, csv_text: str, source: str = "FIRMS") -> dict[str, int]:
    points = parse_firms_csv(csv_text)
    zones = list_zones(conn)
    writes = 0
    events = 0

    for idx, point in enumerate(points):
        if not _inside_bbox(point.latitude, point.longitude, INDIA_BBOX):
            continue

        if point.brightness <= 300.0:
            continue

        zone = nearest_zone(point.latitude, point.longitude, zones)
        event_id = f"firms_{point.acq_date}_{point.acq_time}_{idx}"
        severity = round(min(1.0, point.brightness / 500.0), 3)

        writes += conn.upsertVertex(
            "DisasterEvent",
            event_id,
            {
                "event_type": "fire",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "severity": severity,
                "satellite_source": source,
                "status": "active",
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
