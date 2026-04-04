from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ZoneInfo:
    zone_id: str
    name: str
    centroid_lat: float
    centroid_lng: float
    population_count: int = 0
    disaster_severity: float = 0.0
    is_affected: bool = False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def list_zones(conn: Any) -> list[ZoneInfo]:
    rows = conn.getVertices("Zone", limit=10000)
    zones: list[ZoneInfo] = []
    for row in rows:
        attrs = row.get("attributes", {}) if isinstance(row, dict) else {}
        zone_id = (
            (row.get("v_id") if isinstance(row, dict) else None)
            or attrs.get("zone_id")
            or ""
        )
        if not zone_id:
            continue

        zones.append(
            ZoneInfo(
                zone_id=str(zone_id),
                name=str(attrs.get("name", zone_id)),
                centroid_lat=_to_float(attrs.get("centroid_lat")),
                centroid_lng=_to_float(attrs.get("centroid_lng")),
                population_count=_to_int(attrs.get("population_count")),
                disaster_severity=_to_float(attrs.get("disaster_severity")),
                is_affected=bool(attrs.get("is_affected", False)),
            )
        )
    return zones


def nearest_zone(lat: float, lng: float, zones: list[ZoneInfo]) -> ZoneInfo:
    if not zones:
        raise ValueError("No zones available in TigerGraph.")

    return min(
        zones,
        key=lambda z: haversine_km(lat, lng, z.centroid_lat, z.centroid_lng),
    )


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius_km = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)

    a = (
        math.sin(d_lat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(d_lng / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return radius_km * c
