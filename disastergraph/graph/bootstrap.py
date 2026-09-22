from __future__ import annotations

from disastergraph.config import get_neo4j_connection, get_settings
from disastergraph.graph.schema import create_schema
from disastergraph.graph.seed_data import run_seed


def bootstrap_graph() -> dict[str, object]:
    settings = get_settings()
    conn = get_neo4j_connection(settings)
    schema_result = create_schema(conn)
    seed_result = run_seed(conn=conn)

    return {
        "schema_result": schema_result,
        "seed_result": seed_result,
    }


if __name__ == "__main__":
    print(bootstrap_graph())
