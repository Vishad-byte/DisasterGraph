from __future__ import annotations

import unittest
from unittest.mock import patch

from disastergraph.graph.gsql_exec import pretty_json_or_text
from disastergraph.graph.seed_data import run_seed


class TestGsqlExec(unittest.TestCase):
    def test_pretty_json_or_text_formats_json(self) -> None:
        raw = '{"ok":true,"n":1}'
        rendered = pretty_json_or_text(raw)
        self.assertIn('"ok": true', rendered)
        self.assertIn('"n": 1', rendered)

    def test_pretty_json_or_text_returns_raw_text_for_non_json(self) -> None:
        raw = "not-json"
        rendered = pretty_json_or_text(raw)
        self.assertEqual(raw, rendered)


class TestSeedFallback(unittest.TestCase):
    @patch("disastergraph.graph.seed_data.LOGGER")
    @patch("disastergraph.graph.seed_data.seed_fallback_routes", return_value=12)
    @patch("disastergraph.graph.seed_data.seed_osm_routes", side_effect=RuntimeError("osm down"))
    @patch("disastergraph.graph.seed_data.seed_events", return_value=5)
    @patch("disastergraph.graph.seed_data.seed_resources", return_value=30)
    @patch("disastergraph.graph.seed_data.seed_people", return_value=200)
    @patch("disastergraph.graph.seed_data.upsert_zones")
    @patch("disastergraph.graph.seed_data.generate_demo_zones", return_value=[{"id": "z1"}])
    @patch("disastergraph.graph.seed_data.list_zones", return_value=[])
    @patch("disastergraph.graph.seed_data.get_tg_connection", return_value=object())
    @patch("disastergraph.graph.seed_data.get_settings")
    def test_run_seed_uses_fallback_and_logs_warning(
        self,
        _mock_get_settings,
        _mock_get_conn,
        _mock_list_zones,
        _mock_generate_demo_zones,
        _mock_upsert_zones,
        _mock_seed_people,
        _mock_seed_resources,
        _mock_seed_events,
        _mock_seed_osm_routes,
        mock_seed_fallback_routes,
        mock_logger,
    ) -> None:
        counts = run_seed()

        self.assertEqual(counts["route_writes"], 12)
        mock_seed_fallback_routes.assert_called_once()
        mock_logger.warning.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
