from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from typing import Any

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


@dataclass(slots=True)
class Settings:
    tigergraph_host: str = os.getenv("TG_HOST", "")
    tigergraph_graph: str = os.getenv("TG_GRAPH", "DisasterGraph")
    tigergraph_username: str = os.getenv("TG_USERNAME", "")
    tigergraph_password: str = os.getenv("TG_PASSWORD", "")
    tigergraph_cloud: bool = os.getenv("TG_CLOUD", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    tigergraph_restpp_port: str = os.getenv("TG_RESTPP_PORT", "443")
    tigergraph_gs_port: str = os.getenv("TG_GSQL_PORT", "443")
    tigergraph_secret: str = os.getenv("TG_SECRET", "")

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
    agent_loop_seconds: int = int(os.getenv("AGENT_LOOP_SECONDS", "300"))
    agent_verbose: bool = os.getenv("AGENT_VERBOSE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def get_settings() -> Settings:
    return Settings()


def get_tg_connection(settings: Settings | None = None, include_graph: bool = True) -> Any:
    cfg = settings or get_settings()
    host = (cfg.tigergraph_host or "").strip().strip('"').strip("'")
    if host and not host.startswith(("http://", "https://")):
        host = f"https://{host}"

    if not host:
        raise ValueError("Missing TG_HOST in environment.")

    py_tg = importlib.import_module("pyTigerGraph")
    TigerGraphConnection = getattr(py_tg, "TigerGraphConnection")

    kwargs: dict[str, Any] = {
        "host": host,
        "username": cfg.tigergraph_username,
        "password": cfg.tigergraph_password,
        "tgCloud": cfg.tigergraph_cloud,
        "restppPort": cfg.tigergraph_restpp_port,
        "gsPort": cfg.tigergraph_gs_port,
        "sslPort": "443",
    }
    if include_graph:
        kwargs["graphname"] = cfg.tigergraph_graph
    conn = TigerGraphConnection(**kwargs)

    if cfg.tigergraph_secret:
        try:
            conn.getToken(secret=cfg.tigergraph_secret)
        except Exception:
            pass

    return conn
