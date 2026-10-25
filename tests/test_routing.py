from __future__ import annotations

import unittest
import networkx as nx
from disastergraph.graph.router import (
    RoadRouter,
    encode_polyline,
    decode_polyline,
)
from disastergraph.agent.disaster_agent import DisasterGraphAgent, Assignment


class TestRouting(unittest.TestCase):
    def setUp(self) -> None:
        self.router = RoadRouter.get_instance()

    def test_polyline_encode_decode_round_trip(self) -> None:
        """Verify that Google polyline encoding and decoding round-trips accurately."""
        sample_pts = [
            (28.6139, 77.2090),
            (28.6145, 77.2105),
            (28.6190, 77.2140),
            (28.6250, 77.2200),
        ]
        encoded = encode_polyline(sample_pts)
        self.assertIsInstance(encoded, str)
        self.assertGreater(len(encoded), 5)

        decoded = decode_polyline(encoded)
        self.assertEqual(len(decoded), len(sample_pts))
        for orig, dec in zip(sample_pts, decoded):
            self.assertAlmostEqual(orig[0], dec[0], places=4)
            self.assertAlmostEqual(orig[1], dec[1], places=4)

    def test_nearest_node_snapping(self) -> None:
        """Verify snapping zone centroids / coordinates to nearest road node."""
        lat, lng = 28.6139, 77.2090
        node = self.router.snap_to_node(lat, lng)
        self.assertIn(node, self.router.graph.nodes)

        node_data = self.router.graph.nodes[node]
        n_lat = float(node_data["y"])
        n_lng = float(node_data["x"])

        # Nearest node should be within reasonable urban snapping distance (< 500m / ~0.005 deg)
        deg_dist = ((n_lat - lat) ** 2 + ((n_lng - lng) * 0.88) ** 2) ** 0.5
        self.assertLess(deg_dist, 0.05)

    def test_shortest_path_on_small_fixture_graph(self) -> None:
        """Verify deterministic shortest path on an isolated fixture graph."""
        fixture = nx.MultiDiGraph()
        # Add a diamond network: 1 -> 2 -> 4 (time 10), 1 -> 3 -> 4 (time 20)
        fixture.add_node(1, y=28.50, x=77.10)
        fixture.add_node(2, y=28.51, x=77.11)
        fixture.add_node(3, y=28.52, x=77.09)
        fixture.add_node(4, y=28.53, x=77.10)

        fixture.add_edge(1, 2, 0, length=500.0, travel_time=5.0, safe_cost=5.0, status="clear", is_blocked=False)
        fixture.add_edge(2, 4, 0, length=500.0, travel_time=5.0, safe_cost=5.0, status="clear", is_blocked=False)
        fixture.add_edge(1, 3, 0, length=1000.0, travel_time=10.0, safe_cost=10.0, status="clear", is_blocked=False)
        fixture.add_edge(3, 4, 0, length=1000.0, travel_time=10.0, safe_cost=10.0, status="clear", is_blocked=False)

        path = nx.shortest_path(fixture, 1, 4, weight="travel_time")
        self.assertEqual(path, [1, 2, 4])

    def test_hazard_penalty_changes_chosen_path(self) -> None:
        """Verify that applying an AVOID penalty to the fastest path forces a safer alternative detour."""
        fixture = nx.MultiDiGraph()
        fixture.add_node(1, y=28.50, x=77.10)
        fixture.add_node(2, y=28.51, x=77.11)
        fixture.add_node(3, y=28.52, x=77.09)
        fixture.add_node(4, y=28.53, x=77.10)

        # Path A (through 2): base travel time = 5 + 5 = 10
        fixture.add_edge(1, 2, 0, length=500.0, travel_time=5.0, safe_cost=5.0, status="clear", is_blocked=False)
        fixture.add_edge(2, 4, 0, length=500.0, travel_time=5.0, safe_cost=5.0, status="clear", is_blocked=False)

        # Path B (through 3): base travel time = 7 + 7 = 14
        fixture.add_edge(1, 3, 0, length=700.0, travel_time=7.0, safe_cost=7.0, status="clear", is_blocked=False)
        fixture.add_edge(3, 4, 0, length=700.0, travel_time=7.0, safe_cost=7.0, status="clear", is_blocked=False)

        # Before penalty: fastest path is [1, 2, 4]
        path_before = nx.shortest_path(fixture, 1, 4, weight="safe_cost")
        self.assertEqual(path_before, [1, 2, 4])

        # Apply hazard penalty (avoid) to edge (1, 2): penalty x4 => safe_cost = 20
        fixture[1][2][0]["safe_cost"] = 20.0
        fixture[1][2][0]["status"] = "avoid"

        # After penalty: safe path switches to detour [1, 3, 4] (cost 14 < 25)
        path_after = nx.shortest_path(fixture, 1, 4, weight="safe_cost")
        self.assertEqual(path_after, [1, 3, 4])

    def test_closed_roads_are_never_used(self) -> None:
        """Verify that CLOSED edges are completely excluded from safe path routing."""
        fixture = nx.MultiDiGraph()
        fixture.add_node(1, y=28.50, x=77.10)
        fixture.add_node(2, y=28.51, x=77.11)
        fixture.add_node(3, y=28.52, x=77.09)
        fixture.add_node(4, y=28.53, x=77.10)

        # Path A is closed
        fixture.add_edge(1, 2, 0, length=500.0, travel_time=5.0, safe_cost=float("inf"), status="closed", is_blocked=True)
        fixture.add_edge(2, 4, 0, length=500.0, travel_time=5.0, safe_cost=5.0, status="clear", is_blocked=False)

        # Path B is clear
        fixture.add_edge(1, 3, 0, length=800.0, travel_time=8.0, safe_cost=8.0, status="clear", is_blocked=False)
        fixture.add_edge(3, 4, 0, length=800.0, travel_time=8.0, safe_cost=8.0, status="clear", is_blocked=False)

        # Safe view excluding closed edges
        safe_sub = nx.subgraph_view(
            fixture,
            filter_edge=lambda u, v, k: not fixture[u][v][k].get("is_blocked")
            and fixture[u][v][k].get("status") != "closed",
        )
        safe_path = nx.shortest_path(safe_sub, 1, 4, weight="safe_cost")
        self.assertEqual(safe_path, [1, 3, 4])
        self.assertNotIn(2, safe_path)

    def test_no_safe_route_case(self) -> None:
        """Verify that when all paths are closed, no safe route is detected without crashing."""
        fixture = nx.MultiDiGraph()
        fixture.add_node(1, y=28.50, x=77.10)
        fixture.add_node(2, y=28.51, x=77.11)

        # The only edge is closed
        fixture.add_edge(1, 2, 0, length=500.0, travel_time=5.0, safe_cost=float("inf"), status="closed", is_blocked=True)

        safe_sub = nx.subgraph_view(
            fixture,
            filter_edge=lambda u, v, k: not fixture[u][v][k].get("is_blocked")
            and fixture[u][v][k].get("status") != "closed",
        )
        with self.assertRaises(nx.NetworkXNoPath):
            nx.shortest_path(safe_sub, 1, 2, weight="safe_cost")

    def test_agent_output_rejected_when_candidate_not_provided(self) -> None:
        """Verify deterministic code overrides agent hallucination if candidate is not in candidate set."""
        agent = DisasterGraphAgent()

        # Mock LLM returning a completely hallucinated resource ID
        class MockHallucinatingLLM:
            def complete_json(self, prompt: str) -> str:
                return '{"resource_id": "hallucinated_drone_999", "route_description": "Flying directly through zone", "action": "Air drop", "eta": 1.0}'

        agent.llm = MockHallucinatingLLM()  # type: ignore

        candidates = [
            {
                "resource_id": "res_001",
                "resource_name": "Ambulance 1",
                "eta_min": 14.5,
                "distance_km": 7.2,
                "extra_minutes": 0.0,
                "uses_hazard_road": False,
                "no_safe_route": False,
            },
            {
                "resource_id": "res_002",
                "resource_name": "Ambulance 2",
                "eta_min": 18.2,
                "distance_km": 9.4,
                "extra_minutes": 2.0,
                "uses_hazard_road": True,
                "no_safe_route": False,
            },
        ]

        assignment = agent.generate_assignment(
            zone={"id": "zone_001", "name": "Delhi Ward 1", "disaster_severity": 0.8},
            victims=[{"id": "person_001", "name": "Test Resident"}],
            resources=[{"id": "res_001"}, {"id": "res_002"}],
            route={"path": ["zone_001", "zone_002"], "total_time": 15.0},
            candidates=candidates,
        )

        # The hallucinated ID MUST be rejected, and enforced to top valid candidate res_001!
        self.assertEqual(assignment.resource_id, "res_001")
        # ETA must be deterministic 14.5, NOT the hallucinated 1.0
        self.assertEqual(assignment.eta, 14.5)


if __name__ == "__main__":
    unittest.main()
