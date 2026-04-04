from __future__ import annotations

from disastergraph.config import get_settings, get_tg_connection
from disastergraph.graph.queries import install_queries
from disastergraph.graph.schema import create_schema
from disastergraph.graph.seed_data import run_seed


def bootstrap_graph() -> dict[str, object]:
    settings = get_settings()
    conn_global = get_tg_connection(settings, include_graph=False)
    schema_result = create_schema(conn_global)

    conn = get_tg_connection(settings, include_graph=True)
    query_result = install_queries(conn)
    seed_result = run_seed(conn=conn)

    return {
        "schema_result": schema_result,
        "query_result": query_result,
        "seed_result": seed_result,
    }


if __name__ == "__main__":
    print(bootstrap_graph())
