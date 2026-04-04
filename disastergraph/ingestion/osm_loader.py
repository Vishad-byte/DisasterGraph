from __future__ import annotations

from typing import Any

from disastergraph.graph.seed_data import seed_osm_routes
from disastergraph.graph.utils import list_zones


def ingest_osm_roads(conn: Any, max_edges: int = 1200) -> dict[str, int]:
    zones = list_zones(conn)
    writes = seed_osm_routes(conn, zones, max_edges=max_edges)
    return {"route_writes": writes, "zones_used": len(zones)}
