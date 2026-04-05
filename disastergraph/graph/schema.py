from __future__ import annotations

from typing import Any

from disastergraph.graph.gsql_exec import run_gsql_statement


SCHEMA_STATEMENTS = [
    "USE GLOBAL",
    "CREATE VERTEX Person (PRIMARY_ID id STRING, name STRING, location_lat FLOAT, location_lng FLOAT, vulnerability_score FLOAT, medical_needs STRING, mobility INT)",
    "CREATE VERTEX Zone (PRIMARY_ID zone_id STRING, name STRING, centroid_lat FLOAT, centroid_lng FLOAT, population_count INT, disaster_severity FLOAT, is_affected BOOL)",
    "CREATE VERTEX Resource (PRIMARY_ID res_id STRING, resource_type STRING, capacity INT, current_load INT, location_lat FLOAT, location_lng FLOAT, is_available BOOL)",
    "CREATE VERTEX Route (PRIMARY_ID route_id STRING, start_zone STRING, end_zone STRING, distance_km FLOAT, estimated_time_min FLOAT, is_blocked BOOL, blockage_reason STRING)",
    "CREATE VERTEX DisasterEvent (PRIMARY_ID event_id STRING, event_type STRING, timestamp DATETIME, severity FLOAT, satellite_source STRING, status STRING)",
    "CREATE VERTEX Officer (PRIMARY_ID officer_id STRING, name STRING, telegram_chat_id STRING, zone STRING)",
    "CREATE DIRECTED EDGE located_in (FROM Person, TO Zone)",
    "CREATE DIRECTED EDGE affects (FROM DisasterEvent, TO Zone, severity FLOAT)",
    "CREATE DIRECTED EDGE serves (FROM Resource, TO Zone, coverage_radius_km FLOAT)",
    "CREATE DIRECTED EDGE connects (FROM Zone, TO Zone, route_id STRING, distance_km FLOAT, estimated_time_min FLOAT, is_blocked BOOL, blockage_reason STRING)",
    "CREATE DIRECTED EDGE assigned_to (FROM Resource, TO Person, assigned_at DATETIME, eta_min FLOAT)",
    "CREATE DIRECTED EDGE escalated_from (FROM Zone, TO Zone)",
    "CREATE DIRECTED EDGE manages (FROM Officer, TO Zone)",
    "CREATE GRAPH DisasterGraph(*)",
]


ALTER_STATEMENTS = [
    "USE GLOBAL",
]


def create_schema(conn: Any) -> str:
    """Create DisasterGraph schema and graph types if graph does not exist."""
    listing = run_gsql_statement(conn, "USE GLOBAL\nls")
    outputs: list[str] = []
    bootstrap_statements = SCHEMA_STATEMENTS
    if "DisasterGraph" in listing:
        bootstrap_statements = ALTER_STATEMENTS

    for statement in bootstrap_statements:
        out = run_gsql_statement(conn, statement)
        low = out.lower()
        if "semantic check fails" in low and "used by another object" in low:
            outputs.append(f"[skip-existing] {statement}")
            continue
        if "already exists" in low or "has already been created" in low:
            outputs.append(f"[skip-existing] {statement}")
            continue
        outputs.append(out)
    return "\n".join(outputs)
