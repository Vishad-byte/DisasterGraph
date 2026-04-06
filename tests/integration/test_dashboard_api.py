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
                        "centroid_lat": 28.6139,
                        "centroid_lng": 77.2090,
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
                        "is_available": True,
                        "capacity": 4,
                        "current_load": 1,
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
        if vertex_type == "Zone" and vertex_id == "zone_001" and edge_type == "connects":
            return [
                {
                    "to_id": "zone_001",
                    "attributes": {
                        "route_id": "route_001",
                        "distance_km": 3.2,
                        "estimated_time_min": 11.0,
                        "is_blocked": False,
                        "blockage_reason": "",
                    },
                }
            ]
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

    @patch("disastergraph.dashboard.app._conn", return_value=FakeDashboardConn())
    def test_map_data_returns_zone_points_and_event_links(self, _mock_conn) -> None:
        with TestClient(app) as client:
            resp = client.get("/map/data")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["meta"]["zone_count"], 1)
        self.assertEqual(payload["meta"]["affected_zone_count"], 1)
        self.assertEqual(payload["meta"]["active_event_count"], 1)
        self.assertEqual(payload["zones"][0]["id"], "zone_001")
        self.assertEqual(payload["active_events"][0]["event_id"], "event_001")

    def test_map_live_returns_html_page(self) -> None:
        with TestClient(app) as client:
            resp = client.get("/map/live")

        self.assertEqual(resp.status_code, 200)
        self.assertIn("DisasterGraph Live Risk Map", resp.text)
        self.assertIn("/map/data", resp.text)

    @patch("disastergraph.dashboard.app._conn", return_value=FakeDashboardConn())
    def test_ui_overview_returns_aggregated_live_payload(self, _mock_conn) -> None:
        with TestClient(app) as client:
            resp = client.get("/ui-api/overview")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["meta"]["zone_count"], 1)
        self.assertEqual(payload["meta"]["affected_zone_count"], 1)
        self.assertEqual(payload["meta"]["active_event_count"], 1)
        self.assertEqual(payload["meta"]["assignment_count"], 0)
        self.assertEqual(payload["meta"]["resource_count"], 1)
        self.assertEqual(payload["meta"]["available_resource_count"], 1)
        self.assertEqual(payload["meta"]["resources_by_type"]["ambulance"], 1)

    @patch(
        "disastergraph.dashboard.app._http_get_json",
        return_value={
            "status": "ok",
            "scheduler_running": True,
            "firms_job": True,
            "sentinel_job": True,
        },
    )
    def test_ui_health_returns_dashboard_and_ingestion_status(self, _mock_get) -> None:
        with TestClient(app) as client:
            resp = client.get("/ui-api/health")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["dashboard"]["status"], "ok")
        self.assertEqual(payload["ingestion"]["status"], "ok")
        self.assertEqual(payload["ingestion"]["scheduler_running"], True)

    @patch("disastergraph.dashboard.app._http_post_json", return_value={"firms": {"events": 2}})
    def test_ui_ingest_proxy_run_all_endpoint(self, _mock_post) -> None:
        with TestClient(app) as client:
            resp = client.post("/ui-api/ingest/run_all")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"firms": {"events": 2}})
        _mock_post.assert_called_once()
        self.assertIn("/ingest/run_all", _mock_post.call_args.args[0])

    @patch("disastergraph.dashboard.app._http_post_json", return_value={"events": 3, "zones": 2})
    def test_ui_ingest_proxy_firms_endpoint(self, _mock_post) -> None:
        with TestClient(app) as client:
            resp = client.post("/ui-api/ingest/firms")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"events": 3, "zones": 2})
        _mock_post.assert_called_once()
        self.assertIn("/ingest/firms", _mock_post.call_args.args[0])

    @patch("disastergraph.dashboard.app._http_post_json", return_value={"events": 4, "zones": 1})
    def test_ui_ingest_proxy_sentinel_endpoint(self, _mock_post) -> None:
        with TestClient(app) as client:
            resp = client.post("/ui-api/ingest/sentinel")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"events": 4, "zones": 1})
        _mock_post.assert_called_once()
        self.assertIn("/ingest/sentinel", _mock_post.call_args.args[0])

    @patch("disastergraph.dashboard.app._http_post_json", return_value={"routes": 42, "zones": 60})
    def test_ui_ingest_proxy_osm_roads_endpoint(self, _mock_post) -> None:
        with TestClient(app) as client:
            resp = client.post("/ui-api/ingest/osm_roads")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"routes": 42, "zones": 60})
        _mock_post.assert_called_once()
        self.assertIn("/ingest/osm_roads", _mock_post.call_args.args[0])

    @patch(
        "disastergraph.dashboard.app._http_post_json",
        return_value={
            "firms_poll": {"triggered": True, "job": "firms_poll"},
            "sentinel_poll": {"triggered": True, "job": "sentinel_poll"},
        },
    )
    def test_ui_ingest_proxy_scheduler_trigger_endpoint(self, _mock_post) -> None:
        with TestClient(app) as client:
            resp = client.post("/ui-api/ingest/scheduler/trigger")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["firms_poll"]["triggered"], True)
        self.assertEqual(payload["sentinel_poll"]["triggered"], True)
        _mock_post.assert_called_once()
        self.assertIn("/ingest/scheduler/trigger", _mock_post.call_args.args[0])

    @patch(
        "disastergraph.dashboard.app.run_detection_cycle_once",
        return_value={"ok": True, "message": "Agent detection cycle completed"},
    )
    def test_ui_agent_run_once_endpoint(self, _mock_agent) -> None:
        with TestClient(app) as client:
            resp = client.post("/ui-api/agent/run_once")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["ok"], True)
        self.assertIn("completed", payload["message"])

    @patch("disastergraph.dashboard.app._conn", return_value=FakeDashboardConn())
    def test_ui_assignments_returns_live_assignments(self, _mock_conn) -> None:
        with TestClient(app) as client:
            resp = client.get("/ui-api/assignments")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["assignments"][0]["resource_id"], "res_001")

    @patch("disastergraph.dashboard.app._conn", return_value=FakeDashboardConn())
    def test_ui_routes_returns_route_segments(self, _mock_conn) -> None:
        with TestClient(app) as client:
            resp = client.get("/ui-api/routes")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["blocked_count"], 0)
        self.assertEqual(payload["routes"][0]["route_id"], "route_001")


if __name__ == "__main__":
    unittest.main(verbosity=2)
