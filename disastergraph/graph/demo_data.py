from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from disastergraph.graph.utils import list_zones, nearest_zone


def load_delhi_flood_2023(conn: Any, csv_path: str | Path) -> dict[str, int]:
    path = Path(csv_path)
    if not path.exists():
        return {"events": 0, "writes": 0}

    zones = list_zones(conn)
    writes = 0
    events = 0

    with path.open("r", encoding="utf-8") as fp:
        reader = csv.DictReader(fp)
        for idx, row in enumerate(reader):
            try:
                lat = float(row.get("latitude", 0.0))
                lng = float(row.get("longitude", 0.0))
                severity = float(row.get("severity", 0.7))
            except (TypeError, ValueError):
                continue

            zone = nearest_zone(lat, lng, zones)
            event_id = str(row.get("event_id") or f"delhi_flood_2023_{idx:04d}")
            date_text = str(row.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))

            writes += conn.upsertVertex(
                "DisasterEvent",
                event_id,
                {
                    "event_key": event_id,
                    "event_type": "flood",
                    "timestamp": date_text,
                    "severity": severity,
                    "satellite_source": "IMD-Delhi-2023-cache",
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
