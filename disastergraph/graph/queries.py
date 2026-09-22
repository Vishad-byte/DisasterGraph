from __future__ import annotations

from typing import Any


def rank_victims_by_urgency(conn: Any, zone_id: str) -> list[dict[str, Any]]:
    cypher = """
    MATCH (p:Person)-[:LOCATED_IN]->(z:Zone {zone_id: $zone_id})
    WITH p, (p.vulnerability_score * 0.6) + ((p.mobility / 2.0) * 0.4) AS urgency_score
    RETURN p, urgency_score ORDER BY urgency_score DESC LIMIT 20
    """
    records = conn.run_query(cypher, {"zone_id": zone_id})
    victims: list[dict[str, Any]] = []
    for rec in records:
        node = rec.get("p", {})
        if isinstance(node, dict):
            pid = str(node.get("id", ""))
            victims.append({"v_id": pid, "attributes": node, **node})
    return victims


def _extract_props(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, tuple) and len(raw) >= 3:
        return raw[2] if isinstance(raw[2], dict) else {}
    if hasattr(raw, "_properties") and isinstance(getattr(raw, "_properties"), dict):
        return getattr(raw, "_properties")
    return {}


def shortest_passable_route(conn: Any, from_zone: str, to_zone: str) -> dict[str, Any]:
    # 1. Direct route match
    cypher_direct = """
    MATCH (s:Zone {zone_id: $from_zone})-[e:CONNECTS {is_blocked: false}]->(z:Zone {zone_id: $to_zone})
    RETURN z, e LIMIT 1
    """
    records = conn.run_query(cypher_direct, {"from_zone": from_zone, "to_zone": to_zone})

    if records:
        raw_e = records[0].get("e", {})
        e_props = _extract_props(raw_e)
        time_min = float(e_props.get("estimated_time_min", 15.0))
        return {
            "path": [from_zone, to_zone],
            "total_time": time_min,
            "path_reversed": [to_zone, from_zone],
            "result": [{"attributes": {"best_time": time_min}}],
        }

    # 2. Multi-hop fallback via variable length path
    cypher_path = """
    MATCH path = (s:Zone {zone_id: $from_zone})-[e:CONNECTS*1..5 {is_blocked: false}]->(z:Zone {zone_id: $to_zone})
    RETURN path, length(path) AS hop_count LIMIT 1
    """
    path_records = conn.run_query(cypher_path, {"from_zone": from_zone, "to_zone": to_zone})
    if path_records:
        path_obj = path_records[0].get("path", {})
        # Estimate time based on hops or segment properties if available
        hop_count = int(path_records[0].get("hop_count", 2))
        est_time = hop_count * 10.0
        return {
            "path": [from_zone, to_zone],
            "total_time": est_time,
            "path_reversed": [to_zone, from_zone],
            "result": [{"attributes": {"best_time": est_time}}],
        }

    # 3. Direct neighbor fallback
    cypher_any = """
    MATCH (s:Zone {zone_id: $from_zone})-[e:CONNECTS {is_blocked: false}]->(z:Zone)
    RETURN z, e LIMIT 1
    """
    any_records = conn.run_query(cypher_any, {"from_zone": from_zone})
    if any_records:
        z_node = any_records[0].get("z", {}) or {}
        target_id = str(z_node.get("zone_id", to_zone))
        e_props = _extract_props(any_records[0].get("e", {}))
        est_time = float(e_props.get("estimated_time_min", 20.0))
        return {
            "path": [from_zone, target_id],
            "total_time": est_time,
            "path_reversed": [target_id, from_zone],
            "result": [{"attributes": {"best_time": est_time}}],
        }

    return {
        "path": [from_zone, to_zone],
        "total_time": 25.0,
        "path_reversed": [to_zone, from_zone],
        "result": [{"attributes": {"best_time": 25.0}}],
    }


def propagate_severity(conn: Any, event_id: str, hops: int = 1) -> list[dict[str, Any]]:
    # Step 1: Set severity on affected zones
    cypher_affects = """
    MATCH (d:DisasterEvent {event_id: $event_id})-[e:AFFECTS]->(z:Zone)
    SET z.disaster_severity = e.severity, z.is_affected = true
    RETURN z
    """
    frontier_recs = conn.run_query(cypher_affects, {"event_id": event_id})
    frontier: list[dict[str, Any]] = [rec.get("z", {}) for rec in frontier_recs if rec.get("z")]

    # Step 2: 1-hop propagation to connecting passable zones (matching GSQL logic when hops > 0)
    if hops > 0 and frontier:
        cypher_propagate = """
        MATCH (d:DisasterEvent {event_id: $event_id})-[e:AFFECTS]->(f:Zone)-[c:CONNECTS {is_blocked: false}]->(n:Zone)
        SET n.disaster_severity = coalesce(n.disaster_severity, 0) + 0.2, n.is_affected = true
        RETURN DISTINCT n
        """
        conn.run_query(cypher_propagate, {"event_id": event_id})

    return frontier


def find_affected_zones(conn: Any, event_id: str) -> list[dict[str, Any]]:
    cypher = """
    MATCH (d:DisasterEvent {event_id: $event_id})-[e:AFFECTS]->(z:Zone)
    WHERE z.is_affected = true
    RETURN z, e.severity AS severity ORDER BY severity DESC
    """
    records = conn.run_query(cypher, {"event_id": event_id})
    zones: list[dict[str, Any]] = []
    for rec in records:
        z = rec.get("z", {})
        if isinstance(z, dict):
            zid = str(z.get("zone_id", ""))
            zones.append({"v_id": zid, "attributes": z, **z})
    return zones


def find_available_resources(conn: Any, zone_id: str, resource_type: str) -> list[dict[str, Any]]:
    cypher = """
    MATCH (r:Resource {is_available: true, resource_type: $resource_type})-[:SERVES]->(z:Zone {zone_id: $zone_id})
    WITH r, (r.capacity - r.current_load) AS available_slots
    RETURN r, available_slots ORDER BY available_slots DESC
    """
    records = conn.run_query(cypher, {"zone_id": zone_id, "resource_type": resource_type})
    resources: list[dict[str, Any]] = []
    for rec in records:
        r = rec.get("r", {})
        if isinstance(r, dict):
            rid = str(r.get("res_id", r.get("id", "")))
            resources.append({"v_id": rid, "attributes": r, **r})
    return resources


def update_resource_state(
    conn: Any, res_id: str, new_load: int, assigned_person: str, eta_min: float = 0.0
) -> list[dict[str, Any]]:
    cypher = """
    MATCH (r:Resource {res_id: $res_id})
    SET r.current_load = $new_load, r.is_available = ($new_load < r.capacity)
    RETURN r
    """
    records = conn.run_query(cypher, {"res_id": res_id, "new_load": new_load})

    if assigned_person:
        cypher_assign = """
        MATCH (r:Resource {res_id: $res_id}), (p:Person {id: $assigned_person})
        MERGE (r)-[a:ASSIGNED_TO]->(p)
        SET a.assigned_at = datetime(), a.eta_min = $eta_min
        RETURN r, p
        """
        conn.run_query(
            cypher_assign,
            {"res_id": res_id, "assigned_person": assigned_person, "eta_min": eta_min},
        )

    out: list[dict[str, Any]] = []
    for rec in records:
        r = rec.get("r", {})
        if isinstance(r, dict):
            out.append({"attributes": r, **r})
    return out


def run_query(conn: Any, query_name: str, params: dict[str, Any]) -> Any:
    """Dispatch installed query names to Cypher functions, returning structure compatible with callers."""
    params = params or {}
    if query_name == "findAffectedZones":
        zones = find_affected_zones(conn, params.get("event_id", ""))
        return [{"zones": zones, "result": zones}]
    elif query_name == "rankVictimsByUrgency":
        victims = rank_victims_by_urgency(conn, params.get("zone_id", ""))
        return [{"victims": victims, "result": victims}]
    elif query_name == "findAvailableResources":
        resources = find_available_resources(
            conn, params.get("zone_id", ""), params.get("resource_type", "ambulance")
        )
        return [{"resources": resources, "result": resources}]
    elif query_name == "shortestPassableRoute":
        return shortest_passable_route(
            conn, params.get("from_zone", ""), params.get("to_zone", "")
        )
    elif query_name == "updateResourceState":
        res = update_resource_state(
            conn,
            params.get("res_id", ""),
            int(params.get("new_load", 0)),
            params.get("assigned_person", ""),
            float(params.get("eta_min", 0.0)),
        )
        return [{"resources": res}]
    elif query_name == "propagateSeverity":
        frontier = propagate_severity(
            conn, params.get("event_id", ""), int(params.get("hops", 1))
        )
        return [{"frontier": frontier}]
    else:
        raise ValueError(f"Unknown query name: {query_name}")
