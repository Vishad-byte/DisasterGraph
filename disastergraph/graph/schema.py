from __future__ import annotations

from typing import Any

from disastergraph.config import Neo4jConnection, get_neo4j_connection

SCHEMA_CONSTRAINTS = [
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT zone_id IF NOT EXISTS FOR (z:Zone) REQUIRE z.zone_id IS UNIQUE",
    "CREATE CONSTRAINT resource_id IF NOT EXISTS FOR (r:Resource) REQUIRE r.res_id IS UNIQUE",
    "CREATE CONSTRAINT route_id IF NOT EXISTS FOR (rt:Route) REQUIRE rt.route_id IS UNIQUE",
    "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (e:DisasterEvent) REQUIRE e.event_id IS UNIQUE",
    "CREATE CONSTRAINT officer_id IF NOT EXISTS FOR (o:Officer) REQUIRE o.officer_id IS UNIQUE",
    "CREATE INDEX zone_severity IF NOT EXISTS FOR (z:Zone) ON (z.disaster_severity)",
    "CREATE INDEX person_vuln IF NOT EXISTS FOR (p:Person) ON (p.vulnerability_score)",
]


def create_schema(conn: Any = None) -> str:
    """Create Neo4j uniqueness constraints and indexes for DisasterGraph."""
    neo4j_conn: Neo4jConnection = conn or get_neo4j_connection()
    outputs: list[str] = []

    for stmt in SCHEMA_CONSTRAINTS:
        try:
            neo4j_conn.run_query(stmt)
            outputs.append(f"[OK] {stmt}")
        except Exception as exc:
            outputs.append(f"[ERROR] {stmt}: {exc}")

    return "\n".join(outputs)


if __name__ == "__main__":
    print(create_schema())
