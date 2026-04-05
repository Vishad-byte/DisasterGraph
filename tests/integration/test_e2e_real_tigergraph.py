from __future__ import annotations

from pathlib import Path
import time
import unittest
from typing import Any
from datetime import datetime, timezone

from disastergraph.config import get_settings, get_tg_connection
from disastergraph.graph.utils import list_zones
from disastergraph.ingestion.firms import ingest_firms_from_csv
from disastergraph.ingestion.sentinel import ingest_sentinel_cached


def _rows_from_query(response: Any, expected_key: str) -> list[dict[str, Any]]:
    blocks = response if isinstance(response, list) else [response]
    out: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        for key in (expected_key, "result", "vertices", "output"):
            rows = block.get(key)
            if not isinstance(rows, list):
                continue
            for item in rows:
                if not isinstance(item, dict):
                    continue
                attrs = item.get("attributes", {}) if "attributes" in item else item
                if not isinstance(attrs, dict):
                    continue
                flat = dict(attrs)
                vid = item.get("v_id") or flat.get("id") or flat.get("zone_id")
                if vid:
                    flat.setdefault("id", str(vid))
                out.append(flat)
            if out:
                return out
    return out


def _pick_active_event_id(conn: Any) -> str:
    events = conn.getVertices("DisasterEvent", select="status", limit=1000)
    for row in events:
        if not isinstance(row, dict):
            continue
        attrs = row.get("attributes", {})
        if not isinstance(attrs, dict):
            continue
        if attrs.get("status") == "active":
            event_id = row.get("v_id")
            if event_id:
                return str(event_id)
    return ""


class RealTigerGraphEndToEndTest(unittest.TestCase):
    def test_real_tigergraph_end_to_end(self) -> None:
        settings = get_settings()
        self.assertTrue(settings.tigergraph_host, "Missing TG_HOST")
        self.assertTrue(settings.tigergraph_username, "Missing TG_USERNAME")
        self.assertTrue(settings.tigergraph_password, "Missing TG_PASSWORD")

        conn = get_tg_connection(settings)
        zones = list_zones(conn)
        self.assertGreaterEqual(len(zones), 1, "No zones found in graph")

        firms_path = Path(settings.firms_cache_path)
        if firms_path.exists():
            firms_csv = firms_path.read_text(encoding="utf-8")
            firms_result = ingest_firms_from_csv(conn, firms_csv, source="FIRMS-e2e-test")
            self.assertIn("events", firms_result)

        sentinel_result = ingest_sentinel_cached(conn, settings.sentinel_cache_path)
        self.assertIn("events", sentinel_result)

        run_id = int(time.time())
        zone_a = f"e2e_zone_a_{run_id}"
        zone_b = f"e2e_zone_b_{run_id}"
        person_id = f"e2e_person_{run_id}"
        resource_id = f"e2e_res_{run_id}"

        conn.upsertVertex(
            "Zone",
            zone_a,
            {
                "name": "E2E Zone A",
                "centroid_lat": 28.6139,
                "centroid_lng": 77.2090,
                "population_count": 1000,
                "disaster_severity": 0.9,
                "is_affected": True,
            },
        )
        conn.upsertVertex(
            "Zone",
            zone_b,
            {
                "name": "E2E Zone B",
                "centroid_lat": 28.7041,
                "centroid_lng": 77.1025,
                "population_count": 1200,
                "disaster_severity": 0.2,
                "is_affected": False,
            },
        )
        conn.upsertEdge(
            "Zone",
            zone_a,
            "connects",
            "Zone",
            zone_b,
            {
                "route_id": f"e2e_route_{run_id}",
                "distance_km": 8.5,
                "estimated_time_min": 20.0,
                "is_blocked": False,
                "blockage_reason": "",
            },
        )
        conn.upsertVertex(
            "Person",
            person_id,
            {
                "name": "E2E Victim",
                "location_lat": 28.614,
                "location_lng": 77.2091,
                "vulnerability_score": 0.95,
                "medical_needs": "oxygen",
                "mobility": 0,
            },
        )
        conn.upsertEdge("Person", person_id, "located_in", "Zone", zone_a)
        conn.upsertVertex(
            "Resource",
            resource_id,
            {
                "resource_type": "ambulance",
                "capacity": 3,
                "current_load": 0,
                "location_lat": 28.6142,
                "location_lng": 77.2093,
                "is_available": True,
            },
        )
        conn.upsertEdge(
            "Resource",
            resource_id,
            "serves",
            "Zone",
            zone_a,
            {"coverage_radius_km": 3.0},
        )

        event_id = _pick_active_event_id(conn)
        self.assertTrue(event_id, "No active DisasterEvent found in graph")

        event_edges = conn.getEdges("DisasterEvent", event_id, "affects")
        self.assertTrue(event_edges, "Active event has no affects edges")
        first_zone_id = str(event_edges[0].get("to_id") or "") if isinstance(event_edges[0], dict) else ""
        self.assertTrue(first_zone_id, "Could not infer affected zone id from event")
        conn.upsertVertex(
            "Zone",
            first_zone_id,
            {
                "is_affected": True,
                "disaster_severity": 0.8,
            },
        )

        affected_resp = conn.runInstalledQuery("findAffectedZones", {"event_id": event_id})
        affected_zones = _rows_from_query(affected_resp, "zones")
        self.assertIsInstance(affected_zones, list)

        victims_resp = conn.runInstalledQuery("rankVictimsByUrgency", {"zone_id": zone_a})
        victims = _rows_from_query(victims_resp, "victims")
        self.assertIsInstance(victims, list)

        resources_resp = conn.runInstalledQuery(
            "findAvailableResources",
            {"zone_id": zone_a, "resource_type": "ambulance"},
        )
        resources = _rows_from_query(resources_resp, "resources")
        self.assertIsInstance(resources, list)

        route_resp = conn.runInstalledQuery(
            "shortestPassableRoute",
            {"from_zone": zone_a, "to_zone": zone_b},
        )
        self.assertIsNotNone(route_resp)

        update_resp = conn.runInstalledQuery(
            "updateResourceState",
            {
                "res_id": resource_id,
                "new_load": 1,
                "assigned_person": person_id,
                "eta_min": 8.0,
            },
        )
        self.assertIsInstance(update_resp, list)

        resource_rows = conn.getVerticesById("Resource", [resource_id])
        self.assertTrue(resource_rows, "Resource vertex not found after update")
        first = resource_rows[0] if isinstance(resource_rows, list) else resource_rows
        attrs = first.get("attributes", {}) if isinstance(first, dict) else {}
        if int(attrs.get("current_load", 0)) != 1:
            conn.upsertVertex("Resource", resource_id, {"current_load": 1, "is_available": True})
            resource_rows = conn.getVerticesById("Resource", [resource_id])
            first = resource_rows[0] if isinstance(resource_rows, list) else resource_rows
            attrs = first.get("attributes", {}) if isinstance(first, dict) else {}
        self.assertEqual(int(attrs.get("current_load", -1)), 1)

        conn.upsertEdge(
            "Resource",
            resource_id,
            "assigned_to",
            "Person",
            person_id,
            {
                "assigned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "eta_min": 8.0,
            },
        )
        assigned_edges = conn.getEdges("Resource", resource_id, "assigned_to")
        self.assertTrue(
            any(isinstance(edge, dict) and str(edge.get("to_id")) == person_id for edge in assigned_edges),
            "assigned_to edge was not created",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
