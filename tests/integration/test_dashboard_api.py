from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from disastergraph.dashboard.app import app


class FakeDashboardConn:
    def getVertices(self, vertex_type: str, limit: int = 10000):
        if vertex_type == "Zone":
            return [
                {
                    "v_id": "zone_001",
                    "attributes": {
                        "name": "Zone 1",
                        "disaster_severity": 0.9,
                        "is_affected": True,
                    },
                }
            ]
        if vertex_type == "Resource":
            return [
                {
                    "v_id": "res_001",
                    "attributes": {
                        "resource_type": "ambulance",
                        "status": "available",
                    },
                }
            ]
        if vertex_type == "DisasterEvent":
            return [
                {
                    "v_id": "event_001",
                    "attributes": {
                        "event_type": "flood",
                        "status": "active",
                        "severity": 0.95,
                        "satellite_source": "sentinel",
                        "timestamp": "2026-04-05T10:00:00Z",
                    },
                },
                {
                    "v_id": "event_002",
                    "attributes": {
                        "event_type": "fire",
                        "status": "resolved",
                        "severity": 0.2,
                        "satellite_source": "firms",
                        "timestamp": "2026-04-05T09:00:00Z",
                    },
                },
            ]
        return []

    def getEdges(self, vertex_type: str, vertex_id: str, edge_type: str):
        if vertex_type == "DisasterEvent" and vertex_id == "event_001" and edge_type == "affects":
            return [{"to_id": "zone_001"}, {"to_id": "zone_002"}]
        if vertex_type == "Resource" and vertex_id == "res_001" and edge_type == "assigned_to":
            return [
                {
                    "to_id": "person_001",
                    "attributes": {
                        "assigned_at": "2026-04-05T10:05:00Z",
                        "eta_min": 12.5,
                    },
                }
            ]
        return []


class DashboardIntegrationTests(unittest.TestCase):
    @patch("disastergraph.dashboard.app._conn", return_value=FakeDashboardConn())
    def test_graph_snapshot_returns_counts_and_active_events(self, _mock_conn) -> None:
        with TestClient(app) as client:
            resp = client.get("/graph/snapshot")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["meta"]["zone_count"], 1)
        self.assertEqual(payload["meta"]["resource_count"], 1)
        self.assertEqual(payload["meta"]["active_event_count"], 1)
        self.assertEqual(payload["active_events"][0]["event_type"], "flood")

    @patch("disastergraph.dashboard.app._conn", return_value=FakeDashboardConn())
    def test_events_active_includes_affected_zone_count(self, _mock_conn) -> None:
        with TestClient(app) as client:
            resp = client.get("/events/active")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["events"][0]["event_id"], "event_001")
        self.assertEqual(payload["events"][0]["affected_zone_count"], 2)

    @patch("disastergraph.dashboard.app._conn", return_value=FakeDashboardConn())
    def test_assignments_live_returns_current_assignments(self, _mock_conn) -> None:
        with TestClient(app) as client:
            resp = client.get("/assignments/live")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["assignments"][0]["resource_id"], "res_001")
        self.assertEqual(payload["assignments"][0]["person_id"], "person_001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
