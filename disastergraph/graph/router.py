from __future__ import annotations

import hashlib
import logging
import math
import os
import pickle
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np

from disastergraph.config import Settings, get_settings

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Google Polyline Encoding / Decoding (Zero Dependency)
# ---------------------------------------------------------------------------


def encode_polyline(points: list[tuple[float, float]], precision: int = 5) -> str:
    """Encode a sequence of (lat, lng) tuples into Google Polyline format."""
    factor = 10**precision
    result: list[str] = []
    prev_lat = 0
    prev_lng = 0

    for lat, lng in points:
        lat_int = int(round(lat * factor))
        lng_int = int(round(lng * factor))

        d_lat = lat_int - prev_lat
        d_lng = lng_int - prev_lng

        prev_lat = lat_int
        prev_lng = lng_int

        for d in (d_lat, d_lng):
            val = ~(d << 1) if d < 0 else (d << 1)
            while val >= 0x20:
                result.append(chr((0x20 | (val & 0x1F)) + 63))
                val >>= 5
            result.append(chr(val + 63))

    return "".join(result)


def decode_polyline(
    polyline_str: str, precision: int = 5
) -> list[tuple[float, float]]:
    """Decode a Google Polyline string into a sequence of (lat, lng) tuples."""
    factor = 10**precision
    coords: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lng = 0
    length = len(polyline_str)

    while index < length:
        for is_lng in (False, True):
            shift = 0
            result = 0
            while True:
                if index >= length:
                    break
                byte = ord(polyline_str[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append((round(lat / factor, 6), round(lng / factor, 6)))

    return coords


# ---------------------------------------------------------------------------
# Road Edge & Route Data Classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RouteSegment:
    road_name: str
    status: str  # "clear", "avoid", "closed"
    reason: str
    coords: list[tuple[float, float]]
    distance_km: float
    travel_time_sec: float


@dataclass(slots=True)
class ComputedRoute:
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float
    distance_km: float
    eta_min: float
    fastest_eta_min: float
    extra_minutes: float
    uses_hazard_road: bool
    no_safe_route: bool
    routing_source: str  # "osmnx", "osrm", "approximate"
    encoded_geometry: str
    coords: list[tuple[float, float]]
    fastest_coords: list[tuple[float, float]]
    hazard_zones_crossed: list[str]
    road_names: list[str]
    segments: list[dict[str, Any]]
    status_summary: str  # "clear", "avoid", "closed"
    reason: str


# ---------------------------------------------------------------------------
# RoadRouter Engine
# ---------------------------------------------------------------------------


class RoadRouter:
    _instance: RoadRouter | None = None

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self.undirected_graph: nx.MultiGraph | None = None

        # Numpy index for rapid snapping
        self.node_ids: np.ndarray = np.array([], dtype=np.int64)
        self.node_lats: np.ndarray = np.array([], dtype=np.float64)
        self.node_lngs: np.ndarray = np.array([], dtype=np.float64)

        # Edge arrays for fast spatial join with hazards
        self.edge_keys: list[tuple[int, int, Any]] = []
        self.edge_mid_lats: np.ndarray = np.array([], dtype=np.float64)
        self.edge_mid_lngs: np.ndarray = np.array([], dtype=np.float64)

        # Hazard state tracking
        self.hazard_hash: str = ""
        self.hazard_penalty_min, self.hazard_penalty_max = (
            self.settings.get_hazard_penalties()
        )
        self.hazard_buffer_m = self.settings.hazard_buffer_m

        # Route cache: (orig_node, dest_node, hazard_hash) -> ComputedRoute
        self.route_cache: dict[tuple[int, int, str], ComputedRoute] = {}

        self._load_or_build_graph()

    @classmethod
    def get_instance(cls, settings: Settings | None = None) -> RoadRouter:
        if cls._instance is None:
            cls._instance = RoadRouter(settings=settings)
        return cls._instance

    def _load_or_build_graph(self) -> None:
        tracemalloc.start()
        start_time = time.time()

        cache_path = Path(self.settings.road_graph_cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        pickle_sidecar = cache_path.with_suffix(".pkl")

        loaded = False

        # 1. Try loading from fast pickle cache
        if pickle_sidecar.exists():
            try:
                LOGGER.info("Loading road graph from pickle cache: %s", pickle_sidecar)
                with open(pickle_sidecar, "rb") as f:
                    self.graph = pickle.load(f)
                loaded = True
            except Exception as exc:
                LOGGER.warning("Could not load pickle cache (%s): %s", pickle_sidecar, exc)

        # 2. Try loading from GraphML cache
        if not loaded and cache_path.exists():
            try:
                LOGGER.info("Loading road graph from GraphML: %s", cache_path)
                import osmnx as ox

                self.graph = ox.load_graphml(cache_path)
                loaded = True
                # Save pickle sidecar for instant startup next time
                try:
                    with open(pickle_sidecar, "wb") as f:
                        pickle.dump(self.graph, f, protocol=pickle.HIGHEST_PROTOCOL)
                    LOGGER.info("Saved fast pickle sidecar to %s", pickle_sidecar)
                except Exception as pkl_exc:
                    LOGGER.debug("Could not write pickle sidecar: %s", pkl_exc)
            except Exception as exc:
                LOGGER.warning("Could not load GraphML cache (%s): %s", cache_path, exc)

        # 3. Build graph with OSMnx if no cache found
        if not loaded:
            south, west, north, east = self.settings.get_routing_bbox()
            LOGGER.info(
                "Building drivable road network via OSMnx for bbox [%.4f, %.4f, %.4f, %.4f]...",
                south,
                west,
                north,
                east,
            )
            import osmnx as ox

            try:
                self.graph = ox.graph_from_bbox(
                    bbox=(north, south, east, west),
                    network_type="drive",
                    simplify=True,
                )
                LOGGER.info(
                    "OSMnx fetched road graph with %d nodes and %d edges.",
                    len(self.graph.nodes),
                    len(self.graph.edges),
                )
            except Exception as fetch_exc:
                LOGGER.error("Failed to fetch OSMnx graph from bbox: %s", fetch_exc)
                # Fallback to empty or fixture graph
                self.graph = nx.MultiDiGraph()

            # Add speeds and travel times
            if len(self.graph.nodes) > 0:
                try:
                    self.graph = ox.routing.add_edge_speeds(self.graph)
                    self.graph = ox.routing.add_edge_travel_times(self.graph)
                except Exception as attr_exc:
                    LOGGER.warning("Could not add OSMnx edge speeds: %s", attr_exc)

            # Save caches
            try:
                ox.save_graphml(self.graph, cache_path)
                LOGGER.info("Saved GraphML cache: %s", cache_path)
            except Exception as save_exc:
                LOGGER.warning("Could not save GraphML: %s", save_exc)

            try:
                with open(pickle_sidecar, "wb") as f:
                    pickle.dump(self.graph, f, protocol=pickle.HIGHEST_PROTOCOL)
            except Exception:
                pass

        # Ensure base edge speeds and travel_time exist on all edges
        self._ensure_edge_attributes()

        # Build undirected view for emergency routing fallback
        self.undirected_graph = self.graph.to_undirected()

        # Build numpy lookup arrays for nearest node snapping and edge midpoints
        self._build_spatial_indices()

        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        elapsed = time.time() - start_time
        LOGGER.info(
            "Road graph ready in %.2fs | Nodes: %d, Edges: %d | Startup Memory: %.2f MB (Peak: %.2f MB)",
            elapsed,
            len(self.graph.nodes),
            len(self.graph.edges),
            current_mem / (1024 * 1024),
            peak_mem / (1024 * 1024),
        )

    def _ensure_edge_attributes(self) -> None:
        """Guarantee length, speed_kph, travel_time, safe_cost on all edges."""
        for u, v, k, data in self.graph.edges(keys=True, data=True):
            length = float(data.get("length", 100.0) or 100.0)
            if "speed_kph" not in data or not data["speed_kph"]:
                # Default 30 km/h for urban roads
                data["speed_kph"] = 30.0
            speed = float(data["speed_kph"])
            if speed <= 0:
                speed = 30.0
                data["speed_kph"] = speed

            if "travel_time" not in data or not data["travel_time"]:
                data["travel_time"] = round((length / (speed * 1000.0 / 3600.0)), 2)

            data.setdefault("safe_cost", float(data["travel_time"]))
            data.setdefault("status", "clear")
            data.setdefault("reason", "Clear corridor")
            data.setdefault("is_blocked", False)

    def _build_spatial_indices(self) -> None:
        """Vectorized node coordinates and edge midpoints for sub-millisecond lookups."""
        if not self.graph.nodes:
            self.node_ids = np.array([], dtype=np.int64)
            self.node_lats = np.array([], dtype=np.float64)
            self.node_lngs = np.array([], dtype=np.float64)
            return

        ids = []
        lats = []
        lngs = []
        for n, d in self.graph.nodes(data=True):
            ids.append(n)
            lats.append(float(d.get("y", 0.0)))
            lngs.append(float(d.get("x", 0.0)))

        self.node_ids = np.array(ids, dtype=np.int64)
        self.node_lats = np.array(lats, dtype=np.float64)
        self.node_lngs = np.array(lngs, dtype=np.float64)

        # Edge midpoints for spatial hazard matching
        edge_keys = []
        mid_lats = []
        mid_lngs = []
        for u, v, k, data in self.graph.edges(keys=True, data=True):
            u_node = self.graph.nodes.get(u, {})
            v_node = self.graph.nodes.get(v, {})
            u_lat = float(u_node.get("y", 0.0))
            u_lng = float(u_node.get("x", 0.0))
            v_lat = float(v_node.get("y", 0.0))
            v_lng = float(v_node.get("x", 0.0))
            edge_keys.append((u, v, k))
            mid_lats.append((u_lat + v_lat) / 2.0)
            mid_lngs.append((u_lng + v_lng) / 2.0)

        self.edge_keys = edge_keys
        self.edge_mid_lats = np.array(mid_lats, dtype=np.float64)
        self.edge_mid_lngs = np.array(mid_lngs, dtype=np.float64)

    def snap_to_node(self, lat: float, lng: float) -> int:
        """Snap a lat/lng point to the nearest road network node."""
        if len(self.node_ids) == 0:
            raise ValueError("Road network graph has 0 nodes.")

        # Vectorized Euclidean squared distance with longitude cosine scaling (~0.88 for Delhi 28.6 deg)
        d2 = (self.node_lats - lat) ** 2 + ((self.node_lngs - lng) * 0.88) ** 2
        best_idx = int(np.argmin(d2))
        return int(self.node_ids[best_idx])

    def update_hazard_penalties(self, hazards: list[dict[str, Any]]) -> str:
        """
        Deterministic hazard penalty updater.
        For each active hazard, updates road segments within radius + HAZARD_BUFFER_M:
          - CLEAR: normal travel time
          - AVOID: travel_time * (min_penalty + severity * (max_penalty - min_penalty))
          - CLOSED: physical blockage (flood/collapse/fire); infinite cost
        Recomputes only when hazard state hash changes.
        """
        # Build state signature
        sorted_hazards = sorted(
            hazards,
            key=lambda h: str(h.get("event_id") or h.get("id") or ""),
        )
        sig_parts = []
        for h in sorted_hazards:
            h_id = str(h.get("event_id") or h.get("id") or "")
            sev = round(float(h.get("severity") or 0.0), 3)
            lat = round(float(h.get("lat") or h.get("latitude") or 0.0), 4)
            lng = round(float(h.get("lng") or h.get("longitude") or 0.0), 4)
            rad = round(float(h.get("radius_meters") or 1500.0), 1)
            e_type = str(h.get("event_type") or "hazard").lower()
            sig_parts.append(f"{h_id}:{sev}:{lat}:{lng}:{rad}:{e_type}")

        new_hash = hashlib.md5(";".join(sig_parts).encode("utf-8")).hexdigest()
        if new_hash == self.hazard_hash:
            return self.hazard_hash

        LOGGER.info("Updating hazard penalties on road graph for %d hazards (hash %s)...", len(hazards), new_hash[:8])

        # Reset all edges to CLEAR
        for u, v, k, data in self.graph.edges(keys=True, data=True):
            base_time = float(data.get("travel_time", 1.0))
            data["safe_cost"] = base_time
            data["status"] = "clear"
            data["reason"] = "Clear corridor"
            data["is_blocked"] = False

        if len(self.edge_keys) == 0:
            self.hazard_hash = new_hash
            self.route_cache.clear()
            return new_hash

        # Meters to degrees conversion for Delhi latitude (~28.6 N)
        # 1 deg lat = 111,320m, 1 deg lng = 97,740m
        deg_lat_per_m = 1.0 / 111320.0
        deg_lng_per_m = 1.0 / 97740.0

        for h in hazards:
            sev = float(h.get("severity") or 0.0)
            if sev <= 0.05:
                continue

            h_lat = float(h.get("lat") or h.get("latitude") or 0.0)
            h_lng = float(h.get("lng") or h.get("longitude") or 0.0)
            if h_lat == 0.0 and h_lng == 0.0:
                continue

            base_rad = float(h.get("radius_meters") or 1500.0)
            eff_radius_m = base_rad + self.hazard_buffer_m
            e_type = str(h.get("event_type") or "hazard").lower()

            # Classify hazard nature
            is_physical_closure = any(
                w in e_type for w in ["flood", "water", "collapse", "debris", "structural"]
            ) or (sev >= 0.85 and "fire" in e_type)

            # Spatial filter via numpy
            d_lat = self.edge_mid_lats - h_lat
            d_lng = (self.edge_mid_lngs - h_lng) * 0.88
            dist_sq = d_lat**2 + d_lng**2
            rad_deg = eff_radius_m * deg_lat_per_m
            mask = dist_sq <= (rad_deg**2)
            matching_indices = np.where(mask)[0]

            penalty_factor = self.hazard_penalty_min + (sev * (self.hazard_penalty_max - self.hazard_penalty_min))

            for idx in matching_indices:
                u, v, k = self.edge_keys[idx]
                edge_data = self.graph.get_edge_data(u, v, k)
                if not edge_data:
                    continue

                base_time = float(edge_data.get("travel_time", 1.0))

                if is_physical_closure:
                    edge_data["status"] = "closed"
                    edge_data["is_blocked"] = True
                    edge_data["safe_cost"] = float("inf")
                    edge_data["reason"] = f"Physically blocked by {e_type.replace('_', ' ')} (severity {sev:.2f})"
                else:
                    # Only update if worse than existing status
                    if edge_data["status"] != "closed":
                        penalized_cost = base_time * penalty_factor
                        if penalized_cost > edge_data["safe_cost"]:
                            edge_data["status"] = "avoid"
                            edge_data["safe_cost"] = penalized_cost
                            edge_data["reason"] = f"High {e_type.replace('_', ' ')} hazard nearby: delay penalty applied"

        self.hazard_hash = new_hash
        self.route_cache.clear()
        return new_hash

    def compute_route(
        self,
        origin_lat: float,
        origin_lng: float,
        dest_lat: float,
        dest_lng: float,
    ) -> ComputedRoute:
        """
        Compute fastest and recommended safe route between origin and destination.
        Returns full route metrics, decoded coordinates, segment statuses, and polyline.
        """
        # Check cache
        orig_node = self.snap_to_node(origin_lat, origin_lng)
        dest_node = self.snap_to_node(dest_lat, dest_lng)
        cache_key = (orig_node, dest_node, self.hazard_hash)

        if cache_key in self.route_cache:
            return self.route_cache[cache_key]

        # 1. Compute fastest route (ignores hazards, uses base travel_time)
        fastest_path, fastest_coords = self._find_path(orig_node, dest_node, weight="travel_time")

        # 2. Compute recommended safe route (uses safe_cost, avoids closed roads)
        safe_path, safe_coords, no_safe_route = self._find_safe_path(orig_node, dest_node)

        # If safe route has no path due to closures, fall back to fastest with warning
        if no_safe_route or not safe_path:
            chosen_path = fastest_path
            chosen_coords = fastest_coords
            no_safe_route = True
        else:
            chosen_path = safe_path
            chosen_coords = safe_coords

        # Compute route metrics
        distance_km, eta_min, uses_hazard, hazard_zones, road_names, segments = (
            self._analyze_path(chosen_path)
        )
        fastest_dist, fastest_eta, _, _, _, _ = self._analyze_path(fastest_path)

        extra_minutes = max(0.0, round(eta_min - fastest_eta, 1))

        # Overall status summary
        if no_safe_route:
            status_summary = "closed"
            reason = "No safe route available: all corridors pass physical closures or blocked roads"
        elif uses_hazard:
            status_summary = "avoid"
            reason = f"Route passes {len(hazard_zones)} caution zones (+{extra_minutes:.1f} min safety buffer)"
        else:
            status_summary = "clear"
            reason = "Clear and safe corridor along major thoroughfares"

        # Determine routing source
        routing_source = "osmnx"
        if not chosen_coords or len(chosen_coords) < 2:
            chosen_coords = [(origin_lat, origin_lng), (dest_lat, dest_lng)]
            routing_source = "approximate"
            status_summary = "clear"
            reason = "Approximate direct corridor"

        if not fastest_coords or len(fastest_coords) < 2:
            fastest_coords = [(origin_lat, origin_lng), (dest_lat, dest_lng)]

        encoded = encode_polyline(chosen_coords)

        route = ComputedRoute(
            origin_lat=origin_lat,
            origin_lng=origin_lng,
            dest_lat=dest_lat,
            dest_lng=dest_lng,
            distance_km=distance_km,
            eta_min=eta_min,
            fastest_eta_min=fastest_eta,
            extra_minutes=extra_minutes,
            uses_hazard_road=uses_hazard,
            no_safe_route=no_safe_route,
            routing_source=routing_source,
            encoded_geometry=encoded,
            coords=chosen_coords,
            fastest_coords=fastest_coords,
            hazard_zones_crossed=hazard_zones,
            road_names=road_names,
            segments=segments,
            status_summary=status_summary,
            reason=reason,
        )

        self.route_cache[cache_key] = route
        return route

    def _find_path(
        self, orig: int, dest: int, weight: str
    ) -> tuple[list[int], list[tuple[float, float]]]:
        """Find path using directed graph with fallback to undirected view."""
        if orig == dest:
            n_data = self.graph.nodes.get(orig, {})
            pt = (float(n_data.get("y", 0.0)), float(n_data.get("x", 0.0)))
            return [orig], [pt]

        try:
            path = nx.shortest_path(self.graph, orig, dest, weight=weight)
            return path, self._extract_path_coordinates(path)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass

        # Try undirected graph for emergency access / one-way exemption
        if self.undirected_graph is not None:
            try:
                path = nx.shortest_path(self.undirected_graph, orig, dest, weight=weight)
                return path, self._extract_path_coordinates(path)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass

        return [], []

    def _find_safe_path(
        self, orig: int, dest: int
    ) -> tuple[list[int], list[tuple[float, float]], bool]:
        """Find path avoiding closed roads. Returns (path, coords, no_safe_route)."""
        # Edge filter: exclude closed or infinite-cost edges
        def edge_filter(u: int, v: int, k: Any) -> bool:
            data = self.graph.get_edge_data(u, v, k)
            if not data:
                return False
            cost = float(data.get("safe_cost", 0.0))
            if math.isinf(cost) or data.get("is_blocked") or data.get("status") == "closed":
                return False
            return True

        subgraph = nx.subgraph_view(self.graph, filter_edge=edge_filter)
        try:
            path = nx.shortest_path(subgraph, orig, dest, weight="safe_cost")
            return path, self._extract_path_coordinates(path), False
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            pass

        # Try undirected safe view
        if self.undirected_graph is not None:
            undir_sub = nx.subgraph_view(
                self.undirected_graph,
                filter_edge=lambda u, v, k: edge_filter(u, v, k),
            )
            try:
                path = nx.shortest_path(undir_sub, orig, dest, weight="safe_cost")
                return path, self._extract_path_coordinates(path), False
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass

        return [], [], True

    def _extract_path_coordinates(self, path: list[int]) -> list[tuple[float, float]]:
        """Reconstruct curved road geometry from OSMnx edges."""
        if not path:
            return []
        if len(path) == 1:
            n_data = self.graph.nodes.get(path[0], {})
            return [(float(n_data.get("y", 0.0)), float(n_data.get("x", 0.0)))]

        coords: list[tuple[float, float]] = []

        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            edge_dict = self.graph.get_edge_data(u, v) or (
                self.undirected_graph.get_edge_data(u, v) if self.undirected_graph else None
            )

            u_node = self.graph.nodes.get(u, {})
            u_pt = (float(u_node.get("y", 0.0)), float(u_node.get("x", 0.0)))
            v_node = self.graph.nodes.get(v, {})
            v_pt = (float(v_node.get("y", 0.0)), float(v_node.get("x", 0.0)))

            if not edge_dict:
                if not coords or coords[-1] != u_pt:
                    coords.append(u_pt)
                coords.append(v_pt)
                continue

            # Pick best edge key (smallest travel_time or safe_cost)
            best_key = min(edge_dict.keys(), key=lambda k: float(edge_dict[k].get("travel_time", 9999.0)))
            data = edge_dict[best_key]

            geom = data.get("geometry")
            if geom is not None and hasattr(geom, "coords"):
                line_coords = [(float(lat), float(lng)) for lng, lat in geom.coords]
                # Check orientation
                if line_coords:
                    first_dist = (line_coords[0][0] - u_pt[0]) ** 2 + (line_coords[0][1] - u_pt[1]) ** 2
                    last_dist = (line_coords[-1][0] - u_pt[0]) ** 2 + (line_coords[-1][1] - u_pt[1]) ** 2
                    if last_dist < first_dist:
                        line_coords = list(reversed(line_coords))

                for pt in line_coords:
                    if not coords or coords[-1] != pt:
                        coords.append(pt)
            else:
                if not coords or coords[-1] != u_pt:
                    coords.append(u_pt)
                coords.append(v_pt)

        return coords

    def _analyze_path(
        self, path: list[int]
    ) -> tuple[float, float, bool, list[str], list[str], list[dict[str, Any]]]:
        """Compute distance (km), ETA (min), hazard flags, and segment breakdown."""
        if not path or len(path) < 2:
            return 0.0, 0.0, False, [], [], []

        total_dist_m = 0.0
        total_time_sec = 0.0
        uses_hazard = False
        hazard_zones_set: set[str] = set()
        road_names_set: set[str] = set()
        segments: list[dict[str, Any]] = []

        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            edge_dict = self.graph.get_edge_data(u, v) or (
                self.undirected_graph.get_edge_data(u, v) if self.undirected_graph else None
            )
            if not edge_dict:
                continue

            best_key = min(edge_dict.keys(), key=lambda k: float(edge_dict[k].get("travel_time", 9999.0)))
            data = edge_dict[best_key]

            dist = float(data.get("length", 100.0) or 100.0)
            t_sec = float(data.get("travel_time", 5.0) or 5.0)
            status = str(data.get("status", "clear"))
            reason = str(data.get("reason", "Clear corridor"))
            r_name = str(data.get("name") or "").strip()

            total_dist_m += dist
            total_time_sec += t_sec

            if status in {"avoid", "closed"}:
                uses_hazard = True
                hazard_zones_set.add(reason)

            if r_name and r_name.lower() not in {"unnamed", "none", ""}:
                road_names_set.add(r_name)

            segments.append(
                {
                    "from_node": u,
                    "to_node": v,
                    "road_name": r_name or "Local Street",
                    "status": status,
                    "reason": reason,
                    "distance_km": round(dist / 1000.0, 3),
                    "travel_time_sec": round(t_sec, 1),
                }
            )

        dist_km = round(total_dist_m / 1000.0, 2)
        eta_min = round(total_time_sec / 60.0, 1)

        return (
            dist_km,
            eta_min,
            uses_hazard,
            sorted(list(hazard_zones_set)),
            sorted(list(road_names_set))[:8],
            segments,
        )
