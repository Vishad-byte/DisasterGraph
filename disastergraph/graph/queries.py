from __future__ import annotations

from pathlib import Path
from typing import Any

from disastergraph.graph.gsql_exec import run_gsql_statement


QUERY_FILE = Path(__file__).with_name("queries.gsql")


def _split_query_blocks(script: str) -> list[str]:
    lines = script.splitlines()
    blocks: list[str] = []
    current: list[str] = []
    depth = 0

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if current:
                current.append(line)
            continue

        if stripped.startswith("USE GRAPH"):
            continue

        if stripped.startswith("INSTALL QUERY"):
            blocks.append(stripped)
            continue

        if stripped.startswith("CREATE OR REPLACE QUERY") or stripped.startswith("CREATE QUERY"):
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            current.append(line)
            depth += line.count("{") - line.count("}")
            continue

        if current:
            current.append(line)
            depth += line.count("{") - line.count("}")
            if depth <= 0 and stripped.endswith("}"):
                blocks.append("\n".join(current).strip())
                current = []
                depth = 0

    if current:
        blocks.append("\n".join(current).strip())

    return [b for b in blocks if b]


def install_queries(conn: Any) -> str:
    """Install all DisasterGraph GSQL queries from queries.gsql."""
    content = QUERY_FILE.read_text(encoding="utf-8")
    blocks = _split_query_blocks(content)

    outputs: list[str] = []
    for block in blocks:
        outputs.append(run_gsql_statement(conn, f"USE GRAPH {conn.graphname}\n{block}"))
    return "\n".join(outputs)


def reset_draft_queries(conn: Any) -> str:
    names = [
        "findAffectedZones",
        "rankVictimsByUrgency",
        "findAvailableResources",
        "shortestPassableRoute",
        "updateResourceState",
        "propagateSeverity",
        "qvparam",
        "qstrfrom",
        "qstrfrom2",
        "qgetvid",
        "qname",
        "qzone_str",
        "qroute_test",
        "qroute_test2",
        "qupdate_test",
    ]
    outputs: list[str] = []
    for name in names:
        try:
            outputs.append(
                run_gsql_statement(conn, f"USE GRAPH {conn.graphname}\nDROP QUERY {name}")
            )
        except Exception as exc:
            outputs.append(f"drop {name}: {exc}")
    return "\n".join(outputs)


def run_query(conn: Any, query_name: str, params: dict[str, Any]) -> Any:
    """Execute an installed query with parameters."""
    return conn.runInstalledQuery(query_name, params=params)


def get_vertex_seed(conn: Any, vertex_type: str, vertex_id: str) -> list[dict[str, str]]:
    return [{"v_type": vertex_type, "v_id": vertex_id}]
