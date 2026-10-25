from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

_ROAD_GEOMETRIES_CACHE: dict[str, list[list[float]]] | None = None
_CACHE_FILE = Path("data/cache/road_geometries.json")
_GRAPHML_FILE = Path("data/cache/osm_delhi_india_roads.graphml")


def _pair_key(src_id: str, dst_id: str) -> str:
    return f"{src_id}->{dst_id}"


def load_cached_geometries() -> dict[str, list[list[float]]]:
    global _ROAD_GEOMETRIES_CACHE
    if _ROAD_GEOMETRIES_CACHE is not None:
        return _ROAD_GEOMETRIES_CACHE

    if _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _ROAD_GEOMETRIES_CACHE = data
                LOGGER.info("Loaded %d road geometries from %s", len(data), _CACHE_FILE)
                return _ROAD_GEOMETRIES_CACHE
        except Exception as exc:
            LOGGER.warning("Could not read road geometries cache: %s", exc)

    _ROAD_GEOMETRIES_CACHE = {}
    return _ROAD_GEOMETRIES_CACHE


def generate_road_geometries_from_osm(
    zones: list[dict[str, Any]],
    routes: list[dict[str, Any]],
) -> dict[str, list[list[float]]]:
    global _ROAD_GEOMETRIES_CACHE
    geometries: dict[str, list[list[float]]] = {}

    if not _GRAPHML_FILE.exists():
        LOGGER.warning("OSM GraphML file not found at %s", _GRAPHML_FILE)
        return geometries

    try:
        import networkx as nx
        import osmnx as ox
    except ImportError as exc:
        LOGGER.warning("networkx or osmnx missing: %s", exc)
        return geometries

    LOGGER.info("Loading OSM graph from %s...", _GRAPHML_FILE)
    try:
        graph = ox.load_graphml(_GRAPHML_FILE)
    except Exception as exc:
        LOGGER.warning("Failed loading OSM graph: %s", exc)
        return geometries

    node_data: list[tuple[Any, float, float]] = []
    for node, attrs in graph.nodes(data=True):
        y = attrs.get("y")
        x = attrs.get("x")
        if y is not None and x is not None:
            node_data.append((node, float(y), float(x)))

    if not node_data:
        LOGGER.warning("No valid nodes with coordinates in OSM graph")
        return geometries

    LOGGER.info("Mapping %d zones to nearest OSM road network intersections...", len(zones))
    zone_nodes: dict[str, Any] = {}
    for zone in zones:
        zid = str(zone.get("id") or "")
        zlat = zone.get("lat")
        zlng = zone.get("lng")
        if not zid or zlat is None or zlng is None:
            continue

        best_node = min(
            node_data,
            key=lambda item: (item[1] - zlat) ** 2 + (item[2] - zlng) ** 2,
        )[0]
        zone_nodes[zid] = best_node

    LOGGER.info("Computing turn-by-turn street paths for %d route corridors...", len(routes))
    computed_count = 0
    for route in routes:
        src_id = str(route.get("from_zone_id") or "")
        dst_id = str(route.get("to_zone_id") or "")
        if not src_id or not dst_id:
            continue

        key = _pair_key(src_id, dst_id)
        if key in geometries:
            continue

        src_node = zone_nodes.get(src_id)
        dst_node = zone_nodes.get(dst_id)

        if not src_node or not dst_node or src_node == dst_node:
            continue

        try:
            path_nodes = nx.shortest_path(graph, src_node, dst_node, weight="length")
            coords: list[list[float]] = []

            src_lat = route.get("from", {}).get("lat")
            src_lng = route.get("from", {}).get("lng")
            if src_lat is not None and src_lng is not None:
                coords.append([round(float(src_lat), 5), round(float(src_lng), 5)])

            for n in path_nodes:
                ny = graph.nodes[n].get("y")
                nx_coord = graph.nodes[n].get("x")
                if ny is not None and nx_coord is not None:
                    coords.append([round(float(ny), 5), round(float(nx_coord), 5)])

            dst_lat = route.get("to", {}).get("lat")
            dst_lng = route.get("to", {}).get("lng")
            if dst_lat is not None and dst_lng is not None:
                coords.append([round(float(dst_lat), 5), round(float(dst_lng), 5)])

            if len(coords) >= 2:
                geometries[key] = coords
                rev_key = _pair_key(dst_id, src_id)
                if rev_key not in geometries:
                    geometries[rev_key] = list(reversed(coords))
                computed_count += 1
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass

    LOGGER.info("Successfully generated turn-by-turn geometry for %d route pairs", computed_count)

    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(geometries), encoding="utf-8")
        LOGGER.info("Saved road geometries cache to %s", _CACHE_FILE)
    except Exception as exc:
        LOGGER.warning("Could not write geometries cache: %s", exc)

    _ROAD_GEOMETRIES_CACHE = geometries
    return geometries


def get_road_geometry(
    src_id: str,
    dst_id: str,
    src_lat: float,
    src_lng: float,
    dst_lat: float,
    dst_lng: float,
) -> list[list[float]]:
    cache = load_cached_geometries()
    key = _pair_key(src_id, dst_id)
    if key in cache and len(cache[key]) >= 2:
        return cache[key]

    rev_key = _pair_key(dst_id, src_id)
    if rev_key in cache and len(cache[rev_key]) >= 2:
        return list(reversed(cache[rev_key]))

    return [[src_lat, src_lng], [dst_lat, dst_lng]]
