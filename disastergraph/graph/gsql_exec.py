from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def run_gsql_statement(conn: Any, statement: str) -> str:
    """Execute a single GSQL statement via /gsql/v1/statements."""
    stmt = statement.strip()
    if not stmt:
        return ""

    base_url = str(getattr(conn, "gsUrl", "")).rstrip("/")
    if not base_url:
        raise ValueError("Invalid TigerGraph connection: missing gsUrl")

    username = str(getattr(conn, "username", ""))
    password = str(getattr(conn, "password", ""))
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")

    req = Request(
        url=f"{base_url}/gsql/v1/statements",
        method="POST",
        data=stmt.encode("utf-8"),
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "text/plain",
        },
    )
    try:
        with urlopen(req, timeout=180) as resp:
            payload = resp.read().decode("utf-8", errors="replace")
            return payload
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GSQL HTTP {exc.code}: {body}") from exc


def run_gsql_statements(conn: Any, script: str) -> str:
    """Execute a raw GSQL script as-is through /gsql/v1/statements."""
    return run_gsql_statement(conn, script)


def parse_simple_statements(script: str) -> list[str]:
    """Split simple semicolon-delimited statements (non-query bodies)."""
    statements: list[str] = []
    current: list[str] = []
    for ch in script:
        if ch == ";":
            text = "".join(current).strip()
            if text:
                statements.append(text)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def pretty_json_or_text(text: str) -> str:
    try:
        obj = json.loads(text)
        return json.dumps(obj, indent=2)
    except Exception:
        return text
