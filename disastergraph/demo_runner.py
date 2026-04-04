from __future__ import annotations

from disastergraph.graph.bootstrap import bootstrap_graph
from disastergraph.ingestion.demo_scenario import run_demo_ingestion


def run_full_demo_setup() -> dict[str, object]:
    bootstrap = bootstrap_graph()
    ingestion = run_demo_ingestion()
    return {
        "bootstrap": bootstrap,
        "demo_ingestion": ingestion,
    }


if __name__ == "__main__":
    print(run_full_demo_setup())
