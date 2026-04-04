from __future__ import annotations

from pathlib import Path

from disastergraph.config import get_settings, get_tg_connection
from disastergraph.graph.demo_data import load_delhi_flood_2023
from disastergraph.graph.seed_data import run_seed
from disastergraph.ingestion.firms import ingest_firms_from_csv
from disastergraph.ingestion.sentinel import ingest_sentinel_cached


def run_demo_ingestion() -> dict[str, object]:
    settings = get_settings()
    conn = get_tg_connection(settings)

    seed = run_seed(conn=conn)
    delhi_flood = load_delhi_flood_2023(conn, settings.delhi_flood_cache_path)

    firms_path = Path(settings.firms_cache_path)
    if firms_path.exists():
        firms_result = ingest_firms_from_csv(
            conn,
            firms_path.read_text(encoding="utf-8"),
            source="FIRMS-cached-Oct2023",
        )
    else:
        firms_result = {
            "events": 0,
            "writes": 0,
            "warning": f"Cached FIRMS file not found: {firms_path}",
        }

    sentinel_result = ingest_sentinel_cached(conn, settings.sentinel_cache_path)

    return {
        "seed": seed,
        "delhi_flood_2023": delhi_flood,
        "firms_cached": firms_result,
        "sentinel_cached": sentinel_result,
    }


if __name__ == "__main__":
    print(run_demo_ingestion())
