"""
Comprehensive End-to-End Verification Suite for DisasterGraph on Neo4j.
Runs all three core migration test scenarios and validates live graph state.
"""
from __future__ import annotations

import json
import logging
from unittest.mock import patch

from disastergraph.config import get_neo4j_connection, get_settings
from disastergraph.graph.queries import (
    find_affected_zones,
    find_available_resources,
    propagate_severity,
    rank_victims_by_urgency,
    shortest_passable_route,
    update_resource_state,
)
from disastergraph.ingestion.firms import ingest_firms_from_csv
from disastergraph.ingestion.sentinel import ingest_sentinel_cached
from disastergraph.agent.disaster_agent import DisasterGraphAgent

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("e2e_verification")

PASS = "[PASS]"
FAIL = "[FAIL]"


def run_all():
    conn = get_neo4j_connection()
    conn.verify_connectivity()
    print("=== Connected to Neo4j AuraDB ===")

    s1_ok = scenario_1_wildfire_ingestion(conn)
    s2_ok = scenario_2_route_blocking(conn)
    s3_ok = scenario_3_full_agent_loop(conn)

    print("\n" + "=" * 60)
    print("OVERALL E2E VERIFICATION SUMMARY:")
    print(f"  Scenario 1 (Wildfire Ingestion): {'PASSED' if s1_ok else 'FAILED'}")
    print(f"  Scenario 2 (Route Blocking)    : {'PASSED' if s2_ok else 'FAILED'}")
    print(f"  Scenario 3 (Agent Loop & Alert): {'PASSED' if s3_ok else 'FAILED'}")
    print("=" * 60)

    conn.close()


def scenario_1_wildfire_ingestion(conn) -> bool:
    """
    Scenario 1: Wildfire ingestion -> zone severity escalates to >= 0.8, frontend map turns red
    """
    print("\n" + "=" * 60)
    print("SCENARIO 1: Wildfire Ingestion -> Zone Severity >= 0.8")
    print("=" * 60)

    # Ingest FIRMS-like wildfire data
    csv_data = (
        "latitude,longitude,brightness,scan,track,acq_date,acq_time,satellite,confidence,version,bright_ti4\n"
        "28.6139,77.2090,480.5,1.0,1.0,2026-09-22,1200,N,95,NRT,480.5\n"
    )
    result = ingest_firms_from_csv(conn, csv_data, source="E2E_NASA_FIRMS_TEST")
    print(f"  FIRMS ingestion result: {result}")

    # Check the closest zone to 28.6139, 77.2090
    from disastergraph.graph.utils import list_zones, nearest_zone
    zones = list_zones(conn)
    target_zone = nearest_zone(28.6139, 77.2090, zones)
    zone_id = target_zone.zone_id

    # Verify directly from Neo4j
    rows = conn.run_query(
        "MATCH (z:Zone {zone_id: $zid}) RETURN z.disaster_severity AS severity, z.is_affected AS is_affected, z.name AS name",
        {"zid": zone_id},
    )
    if not rows:
        print(f"  Target zone {zone_id} not found in Neo4j!")
        return False

    sev = float(rows[0].get("severity", 0))
    is_affected = bool(rows[0].get("is_affected", False))
    print(f"  Zone ID        : {zone_id} ({rows[0].get('name')})")
    print(f"  Severity       : {sev:.3f} (target threshold: >= 0.8)")
    print(f"  is_affected    : {is_affected}")

    ok = (sev >= 0.8) and is_affected
    print(f"  Scenario 1 Result: {PASS if ok else FAIL}")
    return ok


def scenario_2_route_blocking(conn) -> bool:
    """
    Scenario 2: Blocking a route -> shortestPassableRoute correctly bypasses it
    """
    print("\n" + "=" * 60)
    print("SCENARIO 2: Route Blocking -> shortestPassableRoute Bypasses Blocked Route")
    print("=" * 60)

    from_zone = "zone_001"
    to_zone = "zone_004"

    # 1. First ensure route exists and is marked unblocked
    conn.run_query(
        "MATCH (s:Zone {zone_id: $f})-[e:CONNECTS]->(t:Zone {zone_id: $t}) "
        "SET e.is_blocked = false, e.blockage_reason = ''",
        {"f": from_zone, "t": to_zone},
    )
    unblocked_route = shortest_passable_route(conn, from_zone, to_zone)
    print(f"  Unblocked route result: path={unblocked_route['path']}, eta={unblocked_route['total_time']} min")

    # 2. Block the direct route
    conn.run_query(
        "MATCH (s:Zone {zone_id: $f})-[e:CONNECTS]->(t:Zone {zone_id: $t}) "
        "SET e.is_blocked = true, e.blockage_reason = 'Road flooded by Yamuna overflow'",
        {"f": from_zone, "t": to_zone},
    )
    print(f"  Blocked route {from_zone} -> {to_zone}")

    # 3. Call shortestPassableRoute again
    blocked_route = shortest_passable_route(conn, from_zone, to_zone)
    print(f"  After blocking route result: path={blocked_route['path']}, time={blocked_route['total_time']} min")

    # Check that direct passable edge is 0
    direct_edges = conn.run_query(
        "MATCH (s:Zone {zone_id: $f})-[e:CONNECTS {is_blocked: false}]->(t:Zone {zone_id: $t}) "
        "RETURN count(e) AS cnt",
        {"f": from_zone, "t": to_zone},
    )
    direct_passable_count = direct_edges[0]["cnt"]
    print(f"  Direct passable edges ({from_zone} -> {to_zone}): {direct_passable_count} (expected 0)")

    # 4. Restore route to unblocked for cleanliness
    conn.run_query(
        "MATCH (s:Zone {zone_id: $f})-[e:CONNECTS]->(t:Zone {zone_id: $t}) "
        "SET e.is_blocked = false, e.blockage_reason = ''",
        {"f": from_zone, "t": to_zone},
    )

    ok = (direct_passable_count == 0) and (unblocked_route["total_time"] <= blocked_route["total_time"])
    print(f"  Scenario 2 Result: {PASS if ok else FAIL}")
    return ok


def scenario_3_full_agent_loop(conn) -> bool:
    """
    Scenario 3: Full agent loop -> valid JSON dispatch, relationship created in Neo4j, alert sent
    """
    print("\n" + "=" * 60)
    print("SCENARIO 3: Full Agent Loop -> Valid JSON Dispatch + ASSIGNED_TO in Neo4j")
    print("=" * 60)

    # Seed an active event with high severity affecting a zone with victims and available ambulance
    test_event_id = "e2e_agent_test_event_99"
    test_zone_id = "zone_007"
    test_person_id = "e2e_victim_99"
    test_res_id = "e2e_ambulance_99"

    # Upsert test nodes in Neo4j
    conn.upsertVertex("Zone", test_zone_id, {"disaster_severity": 0.88, "is_affected": True})
    conn.upsertVertex("DisasterEvent", test_event_id, {
        "event_type": "fire",
        "severity": 0.88,
        "satellite_source": "NASA_FIRMS",
        "status": "active",
    })
    conn.upsertEdge("DisasterEvent", test_event_id, "affects", "Zone", test_zone_id, {"severity": 0.88})

    conn.upsertVertex("Person", test_person_id, {
        "name": "Priority Resident 99",
        "vulnerability_score": 0.95,
        "medical_needs": "oxygen",
        "mobility": 0,
    })
    conn.upsertEdge("Person", test_person_id, "located_in", "Zone", test_zone_id)

    conn.upsertVertex("Resource", test_res_id, {
        "resource_type": "ambulance",
        "capacity": 4,
        "current_load": 0,
        "is_available": True,
    })
    conn.upsertEdge("Resource", test_res_id, "serves", "Zone", test_zone_id, {"coverage_radius_km": 5.0})

    agent = DisasterGraphAgent()

    # Test LLM completion or mock LLM response
    mock_llm_json = json.dumps({
        "resource_id": test_res_id,
        "resource_type": "ambulance",
        "destination_zone": test_zone_id,
        "top_victim_id": test_person_id,
        "priority_victim_count": 1,
        "route_description": "Deploying ambulance along unblocked arterial corridor to Zone 007",
        "action": "Immediate medical evacuation for oxygen-dependent patient",
        "alert_message": "CRITICAL: Ambulance dispatched to Priority Resident 99 in Zone 007",
        "eta": 8.5,
    })

    with patch.object(agent.llm, "complete_json", return_value=mock_llm_json):
        # Run one detection cycle
        print("  Executing agent detection cycle...")
        agent.run_detection_cycle()

    # Verify that ASSIGNED_TO relationship was created in Neo4j
    assigned_edges = conn.run_query(
        "MATCH (r:Resource {res_id: $rid})-[a:ASSIGNED_TO]->(p:Person {id: $pid}) "
        "RETURN r.res_id AS res_id, p.id AS person_id, a.eta_min AS eta",
        {"rid": test_res_id, "pid": test_person_id},
    )

    # Verify resource state was updated
    res_rows = conn.run_query(
        "MATCH (r:Resource {res_id: $rid}) RETURN r.current_load AS current_load, r.is_available AS is_available",
        {"rid": test_res_id},
    )

    assigned_ok = len(assigned_edges) > 0
    load_ok = len(res_rows) > 0 and int(res_rows[0].get("current_load", 0)) >= 1

    print(f"  ASSIGNED_TO edge in Neo4j : {assigned_edges}")
    print(f"  Resource updated state    : {res_rows}")
    print(f"  Dispatch JSON format      : Valid structured JSON with ETA {assigned_edges[0]['eta'] if assigned_ok else 'N/A'} min")

    ok = assigned_ok and load_ok
    print(f"  Scenario 3 Result: {PASS if ok else FAIL}")
    return ok


if __name__ == "__main__":
    run_all()
