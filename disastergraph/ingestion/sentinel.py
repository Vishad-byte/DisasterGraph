from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from disastergraph.graph.utils import list_zones, nearest_zone


NO2_THRESHOLD = 0.0002


@dataclass(slots=True)
class SentinelPoint:
    latitude: float
    longitude: float
    no2: float


def parse_cached_sentinel_csv(path: str | Path) -> list[SentinelPoint]:
    file_path = Path(path)
    if not file_path.exists():
        return []

    points: list[SentinelPoint] = []
    with file_path.open("r", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            try:
                points.append(
                    SentinelPoint(
                        latitude=float(row.get("latitude", 0.0)),
                        longitude=float(row.get("longitude", 0.0)),
                        no2=float(row.get("no2", row.get("value", 0.0))),
                    )
                )
            except (TypeError, ValueError):
                continue
    return points


def ingest_sentinel_cached(conn: Any, csv_path: str | Path) -> dict[str, int]:
    zones = list_zones(conn)
    points = parse_cached_sentinel_csv(csv_path)
    writes = 0
    events = 0

    for idx, point in enumerate(points):
        if point.no2 <= NO2_THRESHOLD:
            continue

        zone = nearest_zone(point.latitude, point.longitude, zones)
        event_id = f"s5p_no2_{idx}_{zone.zone_id}"
        severity = round(min(1.0, point.no2 / (NO2_THRESHOLD * 10.0)), 3)

        writes += conn.upsertVertex(
            "DisasterEvent",
            event_id,
            {
                "event_key": event_id,
                "event_type": "pollution",
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "severity": severity,
                "satellite_source": "Sentinel-5P-cached",
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
