from __future__ import annotations

import logging
from disastergraph.config import get_neo4j_connection, get_settings
from disastergraph.graph.schema import create_schema
from disastergraph.graph.seed_data import run_seed

LOGGER = logging.getLogger(__name__)


def bootstrap_graph() -> dict[str, object]:
    settings = get_settings()
    conn = get_neo4j_connection(settings)
    schema_result = create_schema(conn)

    is_live = getattr(settings, "data_mode", "demo") == "live"
    seed_result = run_seed(conn=conn)

    if is_live:
        from disastergraph.ingestion.osm_loader import ingest_osm_resources
        try:
            osm_resources = ingest_osm_resources(conn)
            seed_result["osm_resources"] = osm_resources
        except Exception as exc:
            LOGGER.warning("OSM resource ingestion during bootstrap failed: %s", exc)
            seed_result["osm_resources_error"] = str(exc)

    return {
        "schema_result": schema_result,
        "seed_result": seed_result,
    }


if __name__ == "__main__":
    print(bootstrap_graph())
