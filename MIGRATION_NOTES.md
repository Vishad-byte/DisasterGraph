# DisasterGraph Migration Notes: TigerGraph (GSQL) to Neo4j (Cypher)

This document details the architectural migration of DisasterGraph from TigerGraph to Neo4j.

## Summary of Changes

1. **Database Tier**:
   - Replaced TigerGraph Cloud (`pyTigerGraph`) with **Neo4j AuraDB Free** (`neo4j` Python driver).
   - Configured `.env` with `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD`.

2. **Schema & Constraints**:
   - Replaced GSQL global schema definitions with Cypher uniqueness constraints and indexes:
     - `person_id` UNIQUE ON `(p:Person).id`
     - `zone_id` UNIQUE ON `(z:Zone).zone_id`
     - `resource_id` UNIQUE ON `(r:Resource).res_id`
     - `route_id` UNIQUE ON `(rt:Route).route_id`
     - `event_id` UNIQUE ON `(e:DisasterEvent).event_id`
     - `officer_id` UNIQUE ON `(o:Officer).officer_id`
     - Index on `(z:Zone).disaster_severity`
     - Index on `(p:Person).vulnerability_score`
   - Graph entities are merged on primary keys (`MERGE`).

3. **Relationship Naming Convention**:
   - Adopted idiomatic Cypher `SCREAMING_SNAKE_CASE` relationship types:
     - `LOCATED_IN` (Person -> Zone)
     - `AFFECTS` (DisasterEvent -> Zone)
     - `SERVES` (Resource -> Zone)
     - `CONNECTS` (Zone -> Zone)
     - `ASSIGNED_TO` (Resource -> Person)
     - `ESCALATED_FROM` (Zone -> Zone)
     - `MANAGES` (Officer -> Zone)

4. **Query Translations**:
   - **`rankVictimsByUrgency`**:
     ```cypher
     MATCH (p:Person)-[:LOCATED_IN]->(z:Zone {zone_id: $zone_id})
     WITH p, (p.vulnerability_score * 0.6) + ((p.mobility / 2.0) * 0.4) AS urgency_score
     RETURN p, urgency_score ORDER BY urgency_score DESC LIMIT 20
     ```
   - **`shortestPassableRoute`**:
     ```cypher
     MATCH (s:Zone {zone_id: $from_zone})-[e:CONNECTS {is_blocked: false}]->(z:Zone {zone_id: $to_zone})
     RETURN z, e LIMIT 1
     ```
   - **`propagateSeverity`**:
     - *Design Decision*: Maintained 1-hop severity propagation matching original GSQL behavior when `hops > 0`.
     - Sets direct affected zones' severity based on `DisasterEvent` severity, and propagates severity increase (+0.2) to 1-hop passable connected zones.

5. **Python Driver & API Contract**:
   - `Neo4jConnection` implements session management, `run_query()`, `upsertVertex()`, `upsertEdge()`, `getVertices()`, and `getEdges()`.
   - Query function signatures (`rank_victims_by_urgency`, `shortest_passable_route`, `propagate_severity`, `find_affected_zones`, `find_available_resources`, `update_resource_state`) remain identical to preserve compatibility across `disaster_agent.py`, `app.py`, `bot.py`, and ingestion pipelines.
