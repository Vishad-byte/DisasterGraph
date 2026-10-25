from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from disastergraph.agent.llm_client import LLMClient, _clean_json_markdown
from disastergraph.config import Settings
from disastergraph.graph.utils import ZoneInfo
from disastergraph.ingestion.air_quality import (
    AirQualityReading,
    apply_route_penalties_for_gas_plumes,
    ingest_air_quality_readings,
)
from disastergraph.ingestion.eonet import parse_eonet_json, ingest_eonet_data
from disastergraph.ingestion.firms import parse_firms_csv, ingest_firms_from_csv
from disastergraph.ingestion.gdacs import parse_gdacs_geojson, ingest_gdacs_data
from disastergraph.ingestion.open_meteo import (
    parse_meteo_responses,
    ingest_open_meteo_data,
)
from disastergraph.ingestion.osm_loader import (
    load_or_fetch_osm_resources,
    ingest_osm_resources,
)
from disastergraph.ingestion.pruner import prune_stale_events
from disastergraph.ingestion.usgs import parse_usgs_geojson, ingest_usgs_data


class MockNeo4jConn:
    def __init__(self, zones: list[ZoneInfo] | None = None) -> None:
        self.vertices: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.queries_run: list[tuple[str, dict[str, Any]]] = []
        self._zones = zones or [
            ZoneInfo(
                zone_id="zone_001",
                name="Central Delhi",
                centroid_lat=28.6139,
                centroid_lng=77.2090,
                population_count=50000,
                disaster_severity=0.0,
                is_affected=False,
            ),
            ZoneInfo(
                zone_id="zone_002",
                name="North Delhi",
                centroid_lat=28.7041,
                centroid_lng=77.1025,
                population_count=65000,
                disaster_severity=0.0,
                is_affected=False,
            ),
        ]

    def upsertVertex(self, vertex_type: str, vertex_id: str, attributes: dict[str, Any] | None = None) -> int:
        key = f"{vertex_type}:{vertex_id}"
        self.vertices[key] = dict(attributes or {})
        return 1

    def upsertEdge(
        self,
        source_type: str,
        source_id: str,
        edge_type: str,
        target_type: str,
        target_id: str,
        attributes: dict[str, Any] | None = None,
    ) -> int:
        self.edges.append({
            "source": f"{source_type}:{source_id}",
            "edge": edge_type,
            "target": f"{target_type}:{target_id}",
            "attrs": dict(attributes or {}),
        })
        return 1

    def getVertices(self, vertex_type: str, limit: int = 10000, select: str | None = None) -> list[dict[str, Any]]:
        out = []
        for k, v in self.vertices.items():
            if k.startswith(f"{vertex_type}:"):
                vid = k.split(":", 1)[1]
                out.append({"v_id": vid, "attributes": v, **v})
        if vertex_type == "Zone" and not out:
            for z in self._zones:
                out.append({
                    "v_id": z.zone_id,
                    "attributes": {
                        "name": z.name,
                        "centroid_lat": z.centroid_lat,
                        "centroid_lng": z.centroid_lng,
                        "disaster_severity": z.disaster_severity,
                        "is_affected": z.is_affected,
                    },
                })
        return out

    def run_query(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries_run.append((query, parameters or {}))
        if "count(c) AS cnt" in query or "count(rt) AS cnt" in query:
            return [{"cnt": 2}]
        if "count(e) AS deleted_count" in query:
            return [{"deleted_count": 3}]
        if "count(z) AS reset_count" in query:
            return [{"reset_count": 1}]
        return []


class TestLiveIngestionParsers(unittest.TestCase):
    def setUp(self) -> None:
        self.zones = [
            ZoneInfo("zone_001", "Connaught Place", 28.6315, 77.2167, 45000, 0.0, False),
            ZoneInfo("zone_002", "Rohini", 28.7495, 77.0565, 70000, 0.0, False),
        ]
        self.conn = MockNeo4jConn(self.zones)

    def test_firms_parser_and_idempotent_upsert(self) -> None:
        sample_csv = (
            "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti4,frp\n"
            "28.6320,77.2170,450.0,1.0,1.0,2026-10-24,1430,N,90,NRT,450.0,25.4\n"
        )
        points = parse_firms_csv(sample_csv)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0].frp, 25.4)
        self.assertEqual(points[0].confidence, "90")

        # Ingest once
        res1 = ingest_firms_from_csv(self.conn, sample_csv, source="FIRMS")
        self.assertEqual(res1["events"], 1)

        # Verify vertex properties
        event_key = "DisasterEvent:firms_2026-10-24_1430_28.6320_77.2170"
        self.assertIn(event_key, self.conn.vertices)
        attrs = self.conn.vertices[event_key]
        self.assertEqual(attrs["type"], "fire")
        self.assertEqual(attrs["frp"], 25.4)
        self.assertEqual(attrs["confidence"], "90")
        self.assertEqual(attrs["source"], "FIRMS")
        self.assertEqual(attrs["status"], "active")

        # Ingest twice (idempotent poll) -> same vertex key updated, no duplicate vertex keys
        res2 = ingest_firms_from_csv(self.conn, sample_csv, source="FIRMS")
        self.assertEqual(res2["events"], 1)
        matching = [k for k in self.conn.vertices if k.startswith("DisasterEvent:firms_2026-10-24_1430")]
        self.assertEqual(len(matching), 1, "Duplicate poll must not create duplicate node keys")

    def test_usgs_parser_and_ingestion(self) -> None:
        mock_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "us7000test",
                    "properties": {
                        "mag": 4.5,
                        "place": "15 km NNE of Delhi, India",
                        "time": 1729780000000,
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [77.22, 28.64, 10.0],
                    },
                },
                {
                    "type": "Feature",
                    "id": "us7000low",
                    "properties": {
                        "mag": 1.2,
                        "place": "Weak tremor",
                        "time": 1729780000000,
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [77.22, 28.64, 5.0],
                    },
                },
            ],
        }
        # Min magnitude 2.0 should filter out the 1.2 tremor
        quakes = parse_usgs_geojson(mock_geojson, min_magnitude=2.0)
        self.assertEqual(len(quakes), 1)
        self.assertEqual(quakes[0].magnitude, 4.5)

        res = ingest_usgs_data(self.conn, mock_geojson, min_magnitude=2.0)
        self.assertEqual(res["events"], 1)
        event_key = "DisasterEvent:usgs_us7000test"
        self.assertIn(event_key, self.conn.vertices)
        attrs = self.conn.vertices[event_key]
        self.assertEqual(attrs["type"], "earthquake")
        self.assertEqual(attrs["magnitude"], 4.5)
        self.assertEqual(attrs["source"], "USGS")

    def test_eonet_parser_and_ingestion(self) -> None:
        mock_eonet = {
            "events": [
                {
                    "id": "EONET_9999",
                    "title": "Industrial Flood Yamuna Corridor",
                    "categories": [{"id": "floods", "title": "Floods"}],
                    "geometry": [
                        {
                            "date": "2026-10-24T12:00:00Z",
                            "type": "Point",
                            "coordinates": [77.21, 28.63],
                        }
                    ],
                }
            ]
        }
        events = parse_eonet_json(mock_eonet)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "flood")

        res = ingest_eonet_data(self.conn, mock_eonet)
        self.assertEqual(res["events"], 1)
        event_key = "DisasterEvent:eonet_EONET_9999"
        self.assertIn(event_key, self.conn.vertices)
        attrs = self.conn.vertices[event_key]
        self.assertEqual(attrs["type"], "flood")
        self.assertEqual(attrs["source"], "EONET")

    def test_gdacs_parser_and_ingestion(self) -> None:
        mock_gdacs = {
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "eventtype": "FL",
                        "eventid": 102030,
                        "name": "Delhi Urban Flood Alert",
                        "alertlevel": "Orange",
                        "fromdate": "2026-10-24",
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [77.21, 28.63],
                    },
                }
            ]
        }
        alerts = parse_gdacs_geojson(mock_gdacs)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].alert_level, "orange")
        self.assertEqual(alerts[0].severity, 0.75)

        res = ingest_gdacs_data(self.conn, mock_gdacs)
        self.assertEqual(res["events"], 1)
        event_key = "DisasterEvent:gdacs_fl_102030"
        self.assertIn(event_key, self.conn.vertices)
        attrs = self.conn.vertices[event_key]
        self.assertEqual(attrs["type"], "flood")
        self.assertEqual(attrs["severity"], 0.75)
        self.assertEqual(attrs["source"], "GDACS")

    def test_open_meteo_parser_and_ingestion(self) -> None:
        mock_meteo_resps = [
            {
                "current": {
                    "time": "2026-10-24T15:00",
                    "precipitation": 28.0,
                    "rain": 25.0,
                    "wind_speed_10m": 45.0,
                }
            },
            {
                "current": {
                    "time": "2026-10-24T15:00",
                    "precipitation": 0.0,
                    "rain": 0.0,
                    "wind_speed_10m": 5.0,
                }
            },
        ]
        obs = parse_meteo_responses(mock_meteo_resps, self.zones)
        self.assertEqual(len(obs), 2)
        self.assertTrue(obs[0].severity >= 0.7)
        self.assertEqual(obs[1].severity, 0.067)

        res = ingest_open_meteo_data(self.conn, mock_meteo_resps, zones=self.zones, severity_threshold=0.35)
        self.assertEqual(res["events"], 1, "Only observation exceeding threshold should be ingested as event")

    def test_air_quality_route_penalties(self) -> None:
        readings = [
            AirQualityReading(
                zone_id="zone_001",
                latitude=28.6315,
                longitude=77.2167,
                no2=180.0,
                pm2_5=220.0,
                aqi=95.0,
                severity=0.9,
                source="OpenAQ-v3",
                time_iso="2026-10-24 15:00:00",
            )
        ]
        res = ingest_air_quality_readings(self.conn, readings, severity_threshold=0.5)
        self.assertEqual(res["events"], 1)
        self.assertEqual(res["penalties_applied"], 2)

        # Check Cypher call applied route blockage for severe gas leak
        applied_queries = [q[0] for q in self.conn.queries_run if "c.is_blocked" in q[0]]
        self.assertTrue(len(applied_queries) > 0, "Route penalties must execute blockage Cypher")

    def test_osm_resources_and_synthetic_capacity(self) -> None:
        with patch("disastergraph.ingestion.osm_loader.load_or_fetch_osm_resources") as mock_fetch:
            mock_fetch.return_value = [
                {
                    "res_id": "osm_hospital_1234",
                    "resource_type": "hospital",
                    "name": "Lok Nayak Hospital",
                    "location_lat": 28.635,
                    "location_lng": 77.220,
                    "capacity": 150,
                    "capacity_source": "estimated",
                    "current_load": 0,
                    "is_available": True,
                    "source": "OSM",
                }
            ]
            res = ingest_osm_resources(self.conn)
            self.assertEqual(res["hospitals"], 1)
            self.assertEqual(res["ambulances"], 12)

            # Check hospital node in graph
            hosp_key = "Resource:osm_hospital_1234"
            self.assertIn(hosp_key, self.conn.vertices)
            self.assertEqual(self.conn.vertices[hosp_key]["source"], "OSM")
            self.assertEqual(self.conn.vertices[hosp_key]["capacity_source"], "estimated")

            # Check synthetic ambulance in graph
            amb_key = "Resource:synth_amb_001"
            self.assertIn(amb_key, self.conn.vertices)
            self.assertEqual(self.conn.vertices[amb_key]["source"], "synthetic")

    def test_pruner_deletes_stale_events(self) -> None:
        res = prune_stale_events(self.conn, ttl_hours=24)
        self.assertEqual(res["pruned_events"], 3)
        self.assertEqual(res["reset_zones"], 1)

    def test_graceful_failure_across_sources(self) -> None:
        from disastergraph.ingestion.main import poll_firms, poll_usgs
        with patch("disastergraph.ingestion.main._conn", return_value=self.conn):
            with patch("disastergraph.ingestion.main.settings") as mock_set:
                mock_set.firms_map_key = "fake_key"
                mock_set.get_firms_bbox.return_value = (76.78, 28.38, 77.42, 28.92)
                mock_set.get_bbox.return_value = (28.38, 76.78, 28.92, 77.42)
                with patch("disastergraph.ingestion.main.fetch_firms_csv", side_effect=RuntimeError("FIRMS Down")):
                    firms_res = poll_firms()
                    self.assertIn("error", firms_res)

                # USGS succeeds even though FIRMS failed
                with patch("disastergraph.ingestion.usgs.fetch_usgs_feed", return_value={"features": []}):
                    usgs_res = poll_usgs()
                    self.assertEqual(usgs_res["events"], 0)
                    self.assertNotIn("error", usgs_res)

    def test_llm_markdown_fence_cleaner(self) -> None:
        raw_markdown = "```json\n{\"resource_id\": \"res_01\", \"action\": \"evacuate\"}\n```"
        cleaned = _clean_json_markdown(raw_markdown)
        parsed = json.loads(cleaned)
        self.assertEqual(parsed["resource_id"], "res_01")

    def test_blocked_route_reroute_fallback(self) -> None:
        from disastergraph.graph.queries import shortest_passable_route

        # 1. Mock direct route unblocked
        mock_conn = MagicMock()
        mock_conn.run_query.side_effect = [
            [{"z": {"zone_id": "zone_002"}, "e": {"estimated_time_min": 12.0, "is_blocked": False}}],
        ]
        res1 = shortest_passable_route(mock_conn, "zone_001", "zone_002")
        self.assertEqual(res1["total_time"], 12.0)
        self.assertEqual(res1["path"], ["zone_001", "zone_002"])

        # 2. Direct route blocked (returns empty for direct passable query), multi-hop fallback returned
        mock_conn.run_query.side_effect = [
            [],  # direct query returns empty because is_blocked is true
            [{"path": {}, "hop_count": 3}],  # multi-hop query returns detour path
        ]
        res2 = shortest_passable_route(mock_conn, "zone_001", "zone_002")
        self.assertEqual(res2["total_time"], 30.0, "Detour route should have higher penalty/time")
        self.assertEqual(res2["path"], ["zone_001", "zone_002"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
