from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from disastergraph.ingestion.main import app


class IngestionIntegrationTests(unittest.TestCase):
    @patch("disastergraph.ingestion.main.poll_firms", return_value={"events": 3, "zones": 2})
    def test_ingest_firms_endpoint(self, _mock_poll_firms) -> None:
        with TestClient(app) as client:
            resp = client.post("/ingest/firms")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"events": 3, "zones": 2})

    @patch("disastergraph.ingestion.main.poll_sentinel", return_value={"events": 4, "zones": 1})
    def test_ingest_sentinel_endpoint(self, _mock_poll_sentinel) -> None:
        with TestClient(app) as client:
            resp = client.post("/ingest/sentinel")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"events": 4, "zones": 1})

    @patch("disastergraph.ingestion.main._conn", return_value=object())
    @patch("disastergraph.ingestion.main.ingest_osm_roads", return_value={"routes": 10})
    def test_ingest_osm_roads_endpoint(self, _mock_ingest_osm, _mock_conn) -> None:
        with TestClient(app) as client:
            resp = client.post("/ingest/osm_roads")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"routes": 10})

    @patch("disastergraph.ingestion.main.poll_firms", return_value={"events": 3})
    @patch("disastergraph.ingestion.main.poll_sentinel", return_value={"events": 5})
    @patch("disastergraph.ingestion.main._conn", return_value=object())
    @patch("disastergraph.ingestion.main.ingest_osm_roads", return_value={"routes": 11})
    def test_ingest_run_all_endpoint(
        self,
        _mock_ingest_osm,
        _mock_conn,
        _mock_sentinel,
        _mock_firms,
    ) -> None:
        with TestClient(app) as client:
            resp = client.post("/ingest/run_all")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "firms": {"events": 3},
                "sentinel": {"events": 5},
                "osm_roads": {"routes": 11},
            },
        )

    @patch(
        "disastergraph.ingestion.main._fire_scheduler_job",
        side_effect=[
            {"triggered": True, "job": "firms_poll"},
            {"triggered": True, "job": "sentinel_poll"},
        ],
    )
    def test_trigger_scheduler_jobs_endpoint(self, _mock_trigger) -> None:
        with TestClient(app) as client:
            resp = client.post("/ingest/scheduler/trigger")

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["firms_poll"]["triggered"], True)
        self.assertEqual(payload["sentinel_poll"]["triggered"], True)

    @patch("disastergraph.ingestion.main.scheduler")
    def test_ingest_health_endpoint(self, mock_scheduler) -> None:
        mock_scheduler.running = True
        mock_scheduler.get_job.side_effect = [object(), object()]

        with TestClient(app) as client:
            resp = client.get("/ingest/health")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json(),
            {
                "status": "ok",
                "scheduler_running": True,
                "firms_job": True,
                "sentinel_job": True,
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
