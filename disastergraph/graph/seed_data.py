from __future__ import annotations

import random
import importlib
from dataclasses import asdict
from datetime import datetime, timezone
from json import loads
from pathlib import Path
from typing import Any

from disastergraph.config import get_settings, get_tg_connection
from disastergraph.graph.utils import ZoneInfo, haversine_km, list_zones, nearest_zone


def _random_name(prefix: str, index: int) -> str:
    return f"{prefix}-{index:03d}"


def _to_attr_dict(zone: ZoneInfo) -> dict[str, Any]:
    data = asdict(zone)
    return {
        "zone_key": data["zone_id"],
        "name": data["name"],
        "centroid_lat": data["centroid_lat"],
        "centroid_lng": data["centroid_lng"],
        "population_count": data["population_count"],
        "disaster_severity": data["disaster_severity"],
        "is_affected": data["is_affected"],
    }


def generate_demo_zones(count: int = 50) -> list[ZoneInfo]:
    """Generate 50 Delhi/NCR-like ward centroids if no GIS source is present."""
    random.seed(42)
    lat_min, lat_max = 28.38, 28.92
    lng_min, lng_max = 76.78, 77.42

    zones: list[ZoneInfo] = []
    for i in range(count):
        zones.append(
            ZoneInfo(
                zone_id=f"zone_{i+1:03d}",
                name=_random_name("Delhi-Ward", i + 1),
                centroid_lat=round(random.uniform(lat_min, lat_max), 6),
                centroid_lng=round(random.uniform(lng_min, lng_max), 6),
                population_count=random.randint(12000, 90000),
                disaster_severity=0.0,
                is_affected=False,
            )
        )
    return zones


def _polygon_centroid(points: list[list[float]]) -> tuple[float, float]:
    if not points:
        return (28.6139, 77.2090)
    lats = [p[1] for p in points if len(p) >= 2]
    lngs = [p[0] for p in points if len(p) >= 2]
    if not lats or not lngs:
        return (28.6139, 77.2090)
    return (sum(lats) / len(lats), sum(lngs) / len(lngs))


def load_zones_from_wards_geojson(geojson_path: str | Path, count: int = 50) -> list[ZoneInfo]:
    path = Path(geojson_path)
    if not path.exists():
        return []

    payload = loads(path.read_text(encoding="utf-8"))
    features = payload.get("features", []) if isinstance(payload, dict) else []
    zones: list[ZoneInfo] = []

    for idx, feature in enumerate(features[:count], start=1):
        props = feature.get("properties", {}) if isinstance(feature, dict) else {}
        geometry = feature.get("geometry", {}) if isinstance(feature, dict) else {}
        gtype = geometry.get("type", "")
        coords = geometry.get("coordinates", [])

        ring: list[list[float]] = []
        if gtype == "Polygon" and coords:
            ring = coords[0]
        elif gtype == "MultiPolygon" and coords and coords[0]:
            ring = coords[0][0]

        centroid_lat, centroid_lng = _polygon_centroid(ring)
        zone_id = str(
            props.get("zone_id")
            or props.get("ward_id")
            or props.get("WARD_NO")
            or f"zone_{idx:03d}"
        )
        name = str(
            props.get("name")
            or props.get("ward_name")
            or props.get("WARD_NAME")
            or _random_name("Delhi-Ward", idx)
        )
        population_count = int(
            props.get("population")
            or props.get("pop")
            or props.get("POPULATION")
            or random.randint(12000, 90000)
        )

        zones.append(
            ZoneInfo(
                zone_id=zone_id,
                name=name,
                centroid_lat=round(float(centroid_lat), 6),
                centroid_lng=round(float(centroid_lng), 6),
                population_count=population_count,
                disaster_severity=0.0,
                is_affected=False,
            )
        )

    return zones


def upsert_zones(conn: Any, zones: list[ZoneInfo]) -> int:
    total = 0
    for z in zones:
        total += conn.upsertVertex("Zone", z.zone_id, _to_attr_dict(z))
    return total


def seed_people(conn: Any, zones: list[ZoneInfo], count: int = 200) -> int:
    random.seed(7)
    needs = ["none", "insulin", "dialysis", "oxygen", "cardiac", "pregnancy"]
    writes = 0

    for i in range(count):
        zone = random.choice(zones)
        person_id = f"person_{i+1:04d}"
        lat_jitter = random.uniform(-0.01, 0.01)
        lng_jitter = random.uniform(-0.01, 0.01)
        vulnerability = round(min(1.0, max(0.0, random.gauss(0.55, 0.2))), 3)

        writes += conn.upsertVertex(
            "Person",
            person_id,
            {
                "person_key": person_id,
                "name": f"Resident {i+1}",
                "location_lat": zone.centroid_lat + lat_jitter,
                "location_lng": zone.centroid_lng + lng_jitter,
                "vulnerability_score": vulnerability,
                "medical_needs": random.choice(needs),
                "mobility": random.choices([0, 1, 2], weights=[0.6, 0.3, 0.1], k=1)[0],
            },
        )
        writes += conn.upsertEdge("Person", person_id, "located_in", "Zone", zone.zone_id)
    return writes


def seed_resources(conn: Any, zones: list[ZoneInfo]) -> int:
    random.seed(11)
    writes = 0

    resource_plan: list[tuple[str, int]] = [
        ("ambulance", 10),
        ("hospital", 8),
        ("shelter", 12),
    ]

    resource_index = 1
    for resource_type, quantity in resource_plan:
        for _ in range(quantity):
            zone = random.choice(zones)
            res_id = f"res_{resource_index:03d}"
            resource_index += 1

            if resource_type == "ambulance":
                capacity = random.randint(2, 4)
                current_load = random.randint(0, 1)
            elif resource_type == "hospital":
                capacity = random.randint(120, 450)
                current_load = random.randint(20, 180)
            else:
                capacity = random.randint(50, 220)
                current_load = random.randint(0, 70)

            writes += conn.upsertVertex(
                "Resource",
                res_id,
                {
                    "resource_key": res_id,
                    "resource_type": resource_type,
                    "capacity": capacity,
                    "current_load": current_load,
                    "location_lat": zone.centroid_lat + random.uniform(-0.02, 0.02),
                    "location_lng": zone.centroid_lng + random.uniform(-0.02, 0.02),
                    "is_available": current_load < capacity,
                },
            )

            nearby_zones = sorted(
                zones,
                key=lambda z: haversine_km(
                    zone.centroid_lat,
                    zone.centroid_lng,
                    z.centroid_lat,
                    z.centroid_lng,
                ),
            )[:3]
            for nz in nearby_zones:
                radius = round(
                    haversine_km(
                        zone.centroid_lat,
                        zone.centroid_lng,
                        nz.centroid_lat,
                        nz.centroid_lng,
                    )
                    + 2.0,
                    2,
                )
                writes += conn.upsertEdge(
                    "Resource",
                    res_id,
                    "serves",
                    "Zone",
                    nz.zone_id,
                    {"coverage_radius_km": radius},
                )

    return writes


def seed_events(conn: Any, zones: list[ZoneInfo]) -> int:
    random.seed(19)
    writes = 0
    samples = random.sample(zones, k=min(5, len(zones)))

    for index, zone in enumerate(samples, start=1):
        event_id = f"bootstrap_event_{index:02d}"
        severity = round(random.uniform(0.45, 0.95), 3)
        writes += conn.upsertVertex(
            "DisasterEvent",
            event_id,
            {
                "event_key": event_id,
                "event_type": random.choice(["flood", "fire", "pollution"]),
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "severity": severity,
                "satellite_source": "seed",
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
            {
                "disaster_severity": severity,
                "is_affected": True,
            },
        )

    return writes


def _make_route_id(src_zone: str, dst_zone: str, edge_key: Any) -> str:
    key_text = str(edge_key)
    safe_key = "".join(ch if ch.isalnum() else "_" for ch in key_text)
    return f"route_{src_zone}_{dst_zone}_{safe_key}"


def seed_osm_routes(conn: Any, zones: list[ZoneInfo], max_edges: int = 1000) -> int:
    """Load Delhi drivable network and map edges to Zone->Zone routes."""
    nx = importlib.import_module("networkx")
    ox = importlib.import_module("osmnx")

    graph = ox.graph_from_place("Delhi, India", network_type="drive")
    edges = list(graph.edges(keys=True, data=True))

    writes = 0
    for u, v, key, data in edges[:max_edges]:
        src = graph.nodes[u]
        dst = graph.nodes[v]

        src_zone = nearest_zone(float(src["y"]), float(src["x"]), zones)
        dst_zone = nearest_zone(float(dst["y"]), float(dst["x"]), zones)
        if src_zone.zone_id == dst_zone.zone_id:
            continue

        distance_km = round(float(data.get("length", 0.0)) / 1000.0, 3)
        if distance_km <= 0:
            continue

        speed_kmh = 25.0
        eta_min = round((distance_km / speed_kmh) * 60.0, 2)
        route_id = _make_route_id(src_zone.zone_id, dst_zone.zone_id, key)

        writes += conn.upsertVertex(
            "Route",
            route_id,
            {
                "route_key": route_id,
                "start_zone": src_zone.zone_id,
                "end_zone": dst_zone.zone_id,
                "distance_km": distance_km,
                "estimated_time_min": eta_min,
                "is_blocked": False,
                "blockage_reason": "",
            },
        )

        writes += conn.upsertEdge(
            "Zone",
            src_zone.zone_id,
            "connects",
            "Zone",
            dst_zone.zone_id,
            {
                "route_id": route_id,
                "distance_km": distance_km,
                "estimated_time_min": eta_min,
                "is_blocked": False,
                "blockage_reason": "",
            },
        )
    return writes


def seed_fallback_routes(conn: Any, zones: list[ZoneInfo], per_zone_links: int = 3) -> int:
    """Create synthetic passable routes if OSM fetch is unavailable."""
    writes = 0
    for zone in zones:
        neighbors = sorted(
            [z for z in zones if z.zone_id != zone.zone_id],
            key=lambda z: haversine_km(
                zone.centroid_lat,
                zone.centroid_lng,
                z.centroid_lat,
                z.centroid_lng,
            ),
        )[:per_zone_links]

        for idx, nb in enumerate(neighbors, start=1):
            distance_km = round(
                haversine_km(
                    zone.centroid_lat,
                    zone.centroid_lng,
                    nb.centroid_lat,
                    nb.centroid_lng,
                ),
                3,
            )
            if distance_km <= 0:
                continue
            eta_min = round((distance_km / 25.0) * 60.0, 2)
            route_id = _make_route_id(zone.zone_id, nb.zone_id, f"fallback_{idx}")

            writes += conn.upsertVertex(
                "Route",
                route_id,
                {
                    "route_key": route_id,
                    "start_zone": zone.zone_id,
                    "end_zone": nb.zone_id,
                    "distance_km": distance_km,
                    "estimated_time_min": eta_min,
                    "is_blocked": False,
                    "blockage_reason": "",
                },
            )

            writes += conn.upsertEdge(
                "Zone",
                zone.zone_id,
                "connects",
                "Zone",
                nb.zone_id,
                {
                    "route_id": route_id,
                    "distance_km": distance_km,
                    "estimated_time_min": eta_min,
                    "is_blocked": False,
                    "blockage_reason": "",
                },
            )
    return writes


def run_seed(conn: Any | None = None) -> dict[str, int]:
    settings = get_settings()
    conn = conn or get_tg_connection(settings)
    zones = list_zones(conn)
    if len(zones) < 50:
        from_wards = (
            load_zones_from_wards_geojson(settings.census_wards_geojson, count=50)
            if settings.census_wards_geojson
            else []
        )
        zones = from_wards if from_wards else generate_demo_zones(50)
        upsert_zones(conn, zones)
    else:
        zones = zones[:50]
        for z in zones:
            conn.upsertVertex("Zone", z.zone_id, {"zone_key": z.zone_id})

    counts = {
        "zones": len(zones),
        "people_writes": seed_people(conn, zones, 200),
        "resource_writes": seed_resources(conn, zones),
        "event_writes": seed_events(conn, zones),
        "route_writes": 0,
    }

    try:
        counts["route_writes"] = seed_osm_routes(conn, zones)
    except Exception:
        counts["route_writes"] = seed_fallback_routes(conn, zones)

    return counts


if __name__ == "__main__":
    result = run_seed()
    print(result)
