from __future__ import annotations

from disastergraph.config import get_settings, get_tg_connection
from disastergraph.graph.gsql_exec import run_gsql_statement


def try_query(name: str, body: str) -> None:
    settings = get_settings()
    conn = get_tg_connection(settings)
    try:
        print(run_gsql_statement(conn, f"USE GRAPH {conn.graphname}\nDROP QUERY {name}"))
    except Exception as exc:
        print(f"drop {name}: {exc}")

    script = f"USE GRAPH {conn.graphname}\n{body}\nINSTALL QUERY {name}"
    print(run_gsql_statement(conn, script))


def main() -> None:
    tests = {
        "qvparam": """
CREATE QUERY qvparam(VERTEX<DisasterEvent> event_v) FOR GRAPH DisasterGraph {
  out = SELECT z FROM event_v:d -(affects:e)-> Zone:z;
  PRINT out;
}
""",
        "qstrfrom": """
CREATE QUERY qstrfrom(STRING event_id) FOR GRAPH DisasterGraph {
  out = SELECT z FROM event_id:d -(affects:e)-> Zone:z;
  PRINT out;
}
""",
        "qstrfrom2": """
CREATE QUERY qstrfrom2(SET<STRING> event_ids) FOR GRAPH DisasterGraph {
  out = SELECT z FROM event_ids:d -(affects:e)-> Zone:z;
  PRINT out;
}
""",
        "qgetvid": """
CREATE QUERY qgetvid(STRING event_id) FOR GRAPH DisasterGraph {
  out = SELECT z FROM DisasterEvent:d -(affects:e)-> Zone:z WHERE to_string(getvid(d)) == event_id;
  PRINT out;
}
""",
        "qname": """
CREATE QUERY qname(STRING n) FOR GRAPH DisasterGraph {
  out = SELECT p FROM Person:p WHERE p.name == n;
  PRINT out;
}
""",
        "qzone_str": """
CREATE QUERY qzone_str(STRING zone_id) FOR GRAPH DisasterGraph {
  out = SELECT p FROM Person:p -(located_in)-> zone_id:z;
  PRINT out;
}
""",
    }

    for name, body in tests.items():
        print("\n=====", name, "=====")
        try:
            try_query(name, body.strip())
        except Exception as exc:
            print(exc)


if __name__ == "__main__":
    main()
