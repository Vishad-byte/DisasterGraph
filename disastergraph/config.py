from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any

from neo4j import GraphDatabase


def _load_dotenv() -> bool:
    try:
        dotenv = importlib.import_module("dotenv")
    except ModuleNotFoundError:
        return False

    load_dotenv = getattr(dotenv, "load_dotenv", None)
    if callable(load_dotenv):
        return bool(load_dotenv())
    return False


_load_dotenv()


PK_MAP: dict[str, str] = {
    "Person": "id",
    "Zone": "zone_id",
    "Resource": "res_id",
    "Route": "route_id",
    "DisasterEvent": "event_id",
    "Officer": "officer_id",
}

REL_MAP: dict[str, str] = {
    "located_in": "LOCATED_IN",
    "affects": "AFFECTS",
    "serves": "SERVES",
    "connects": "CONNECTS",
    "assigned_to": "ASSIGNED_TO",
    "escalated_from": "ESCALATED_FROM",
    "manages": "MANAGES",
}


@dataclass(slots=True)
class Settings:
    neo4j_uri: str = os.getenv("NEO4J_URI", "")
    neo4j_user: str = os.getenv("NEO4J_USER", os.getenv("NEO4J_USERNAME", "neo4j"))
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")

    claude_api_key: str = os.getenv("CLAUDE_API_KEY", "")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openrouter")
    llm_model: str = os.getenv("LLM_MODEL", "anthropic/claude-3.5-sonnet")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_base_url: str = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )
    openrouter_site_url: str = os.getenv("OPENROUTER_SITE_URL", "")
    openrouter_app_name: str = os.getenv("OPENROUTER_APP_NAME", "DisasterGraph")
    telegram_token: str = os.getenv("TELEGRAM_TOKEN", "")
    firms_map_key: str = os.getenv("FIRMS_MAP_KEY", "")
    officer_chat_ids_json: str = os.getenv("OFFICER_CHAT_IDS", "{}")

    sentinel_cache_path: str = os.getenv(
        "SENTINEL_CACHE_PATH", "data/cache/sentinel_no2.csv"
    )
    firms_cache_path: str = os.getenv("FIRMS_CACHE_PATH", "data/cache/firms.csv")
    delhi_flood_cache_path: str = os.getenv(
        "DELHI_FLOOD_CACHE_PATH", "data/cache/delhi_flood_2023.csv"
    )
    census_wards_geojson: str = os.getenv("CENSUS_WARDS_GEOJSON", "")
    ingestion_service_url: str = os.getenv(
        "INGESTION_SERVICE_URL", "http://127.0.0.1:8001"
    )
    agent_loop_seconds: int = int(os.getenv("AGENT_LOOP_SECONDS", "300"))
    agent_verbose: bool = os.getenv("AGENT_VERBOSE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_settings() -> Settings:
    return Settings()


class Neo4jConnection:
    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self.uri = uri or cfg.neo4j_uri
        self.user = user or cfg.neo4j_user
        self.password = password or cfg.neo4j_password

        if not self.uri:
            raise ValueError("Missing NEO4J_URI in environment.")

        self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def close(self) -> None:
        self.driver.close()

    @staticmethod
    def _sanitize_val(val: Any) -> Any:
        if isinstance(val, dict):
            return {k: Neo4jConnection._sanitize_val(v) for k, v in val.items()}
        elif isinstance(val, (list, tuple, set)):
            return [Neo4jConnection._sanitize_val(x) for x in val]
        elif hasattr(val, "iso_format"):
            return val.iso_format()
        elif hasattr(val, "to_native"):
            return str(val.to_native())
        return val

    def run_query(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.driver.session() as session:
            result = session.run(query, parameters or {})
            return [self._sanitize_val(record.data()) for record in result]

    def verify_connectivity(self) -> None:
        self.driver.verify_connectivity()

    def _pk(self, label: str) -> str:
        return PK_MAP.get(label, "id")

    def _rel(self, edge_type: str) -> str:
        return REL_MAP.get(edge_type, edge_type.upper())

    def upsertVertex(self, vertex_type: str, vertex_id: str, attributes: dict[str, Any] | None = None) -> int:
        pk = self._pk(vertex_type)
        props = dict(attributes or {})
        props[pk] = vertex_id

        cypher = f"MERGE (n:{vertex_type} {{{pk}: $vid}}) SET n += $props RETURN count(n) AS cnt"
        res = self.run_query(cypher, {"vid": vertex_id, "props": props})
        return int(res[0]["cnt"]) if res else 1

    def upsertEdge(
        self,
        source_type: str,
        source_id: str,
        edge_type: str,
        target_type: str,
        target_id: str,
        attributes: dict[str, Any] | None = None,
    ) -> int:
        src_pk = self._pk(source_type)
        dst_pk = self._pk(target_type)
        rel_name = self._rel(edge_type)
        props = dict(attributes or {})

        cypher = (
            f"MATCH (src:{source_type} {{{src_pk}: $src_id}}), (dst:{target_type} {{{dst_pk}: $dst_id}}) "
            f"MERGE (src)-[r:{rel_name}]->(dst) "
            f"SET r += $props RETURN count(r) AS cnt"
        )
        res = self.run_query(cypher, {"src_id": source_id, "dst_id": target_id, "props": props})
        return int(res[0]["cnt"]) if res else 1

    def getVertices(self, vertex_type: str, limit: int = 10000, select: str | None = None) -> list[dict[str, Any]]:
        pk = self._pk(vertex_type)
        cypher = f"MATCH (n:{vertex_type}) RETURN n LIMIT $limit"
        records = self.run_query(cypher, {"limit": limit})
        out: list[dict[str, Any]] = []
        for rec in records:
            node = rec.get("n", {})
            if isinstance(node, dict):
                vid = str(node.get(pk, node.get("id", "")))
                out.append({"v_id": vid, "attributes": node, **node})
        return out

    def getVerticesById(self, vertex_type: str, vertex_ids: list[str]) -> list[dict[str, Any]]:
        pk = self._pk(vertex_type)
        cypher = f"MATCH (n:{vertex_type}) WHERE n.{pk} IN $ids RETURN n"
        records = self.run_query(cypher, {"ids": vertex_ids})
        out: list[dict[str, Any]] = []
        for rec in records:
            node = rec.get("n", {})
            if isinstance(node, dict):
                vid = str(node.get(pk, node.get("id", "")))
                out.append({"v_id": vid, "attributes": node, **node})
        return out

    def getEdges(self, source_type: str, source_id: str, edge_type: str) -> list[dict[str, Any]]:
        src_pk = self._pk(source_type)
        rel_name = self._rel(edge_type)
        cypher = (
            f"MATCH (src:{source_type} {{{src_pk}: $src_id}})-[r:{rel_name}]->(dst) "
            f"RETURN labels(dst)[0] AS to_type, dst, properties(r) AS attributes"
        )
        records = self.run_query(cypher, {"src_id": source_id})
        out: list[dict[str, Any]] = []
        for rec in records:
            dst_node = rec.get("dst", {})
            dst_type = rec.get("to_type", "Zone")
            dst_pk = self._pk(dst_type)
            to_id = str(dst_node.get(dst_pk, dst_node.get("id", "")))
            attrs = rec.get("attributes", {}) or {}
            out.append({
                "from_id": source_id,
                "to_id": to_id,
                "e_type": edge_type,
                "attributes": attrs,
                **attrs,
            })
        return out

    def runInstalledQuery(self, query_name: str, params: dict[str, Any] | None = None) -> Any:
        from disastergraph.graph.queries import run_query
        return run_query(self, query_name, params or {})


def get_neo4j_connection(settings: Settings | None = None) -> Neo4jConnection:
    return Neo4jConnection(settings=settings)


get_tg_connection = get_neo4j_connection
