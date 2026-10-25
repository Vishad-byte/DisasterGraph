from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException

from disastergraph.config import get_neo4j_connection, get_settings
from disastergraph.graph.utils import list_zones
from disastergraph.ingestion.air_quality import (
    fetch_open_meteo_aq_for_zones,
    fetch_openaq_readings,
    ingest_air_quality_readings,
)
from disastergraph.ingestion.eonet import fetch_eonet_events, ingest_eonet_data
from disastergraph.ingestion.firms import fetch_firms_csv, ingest_firms_from_csv
from disastergraph.ingestion.gdacs import fetch_gdacs_alerts, ingest_gdacs_data
from disastergraph.ingestion.open_meteo import (
    fetch_open_meteo_for_zones,
    ingest_open_meteo_data,
)
from disastergraph.ingestion.osm_loader import ingest_osm_resources, ingest_osm_roads
from disastergraph.ingestion.pruner import prune_stale_events
from disastergraph.ingestion.sentinel import ingest_sentinel_cached

LOGGER = logging.getLogger(__name__)

app = FastAPI(title="DisasterGraph Ingestion Service", version="0.2.0")
settings = get_settings()
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")


def _conn() -> Any:
    return get_neo4j_connection(settings)


def poll_firms() -> dict[str, int]:
    conn = _conn()
    if settings.firms_map_key:
        try:
            csv_text = fetch_firms_csv(settings.firms_map_key, bbox=settings.get_firms_bbox())
            return ingest_firms_from_csv(conn, csv_text, source="FIRMS", bbox=settings.get_bbox())
        except Exception as exc:
            LOGGER.error("Live FIRMS fetch failed: %s", exc)
            if settings.data_mode != "live" and Path(settings.firms_cache_path).exists():
                with open(settings.firms_cache_path, "r", encoding="utf-8") as fp:
                    return ingest_firms_from_csv(conn, fp.read(), source="FIRMS-cached")
            return {"events": 0, "writes": 0, "error": str(exc)}
    else:
        if Path(settings.firms_cache_path).exists():
            with open(settings.firms_cache_path, "r", encoding="utf-8") as fp:
                return ingest_firms_from_csv(conn, fp.read(), source="FIRMS-cached")
        LOGGER.info("FIRMS_MAP_KEY not configured and no cache file found.")
        return {"events": 0, "writes": 0}


def poll_usgs() -> dict[str, int]:
    conn = _conn()
    try:
        from disastergraph.ingestion.usgs import fetch_usgs_feed, ingest_usgs_data
        data = fetch_usgs_feed()
        return ingest_usgs_data(conn, data, bbox=settings.get_bbox(), min_magnitude=2.0)
    except Exception as exc:
        LOGGER.error("USGS ingestion failed: %s", exc)
        return {"events": 0, "writes": 0, "error": str(exc)}


def poll_eonet() -> dict[str, int]:
    conn = _conn()
    try:
        data = fetch_eonet_events(bbox=settings.get_bbox())
        return ingest_eonet_data(conn, data, bbox=settings.get_bbox())
    except Exception as exc:
        LOGGER.error("EONET ingestion failed: %s", exc)
        return {"events": 0, "writes": 0, "error": str(exc)}


def poll_gdacs() -> dict[str, int]:
    conn = _conn()
    try:
        data = fetch_gdacs_alerts()
        return ingest_gdacs_data(conn, data, bbox=settings.get_bbox())
    except Exception as exc:
        LOGGER.error("GDACS ingestion failed: %s", exc)
        return {"events": 0, "writes": 0, "error": str(exc)}


def poll_open_meteo() -> dict[str, int]:
    conn = _conn()
    try:
        zones = list_zones(conn)
        responses = fetch_open_meteo_for_zones(zones)
        return ingest_open_meteo_data(conn, responses, zones=zones)
    except Exception as exc:
        LOGGER.error("Open-Meteo ingestion failed: %s", exc)
        return {"events": 0, "writes": 0, "error": str(exc)}


def poll_air_quality() -> dict[str, int]:
    conn = _conn()
    try:
        zones = list_zones(conn)
        if settings.openaq_api_key:
            readings = fetch_openaq_readings(settings.openaq_api_key, settings.get_bbox(), zones)
            if not readings:
                readings = fetch_open_meteo_aq_for_zones(zones)
        else:
            readings = fetch_open_meteo_aq_for_zones(zones)
        return ingest_air_quality_readings(conn, readings)
    except Exception as exc:
        LOGGER.error("Air Quality ingestion failed: %s", exc)
        return {"events": 0, "writes": 0, "error": str(exc)}


def poll_sentinel() -> dict[str, int]:
    conn = _conn()
    return ingest_sentinel_cached(conn, settings.sentinel_cache_path)


def poll_prune() -> dict[str, int]:
    conn = _conn()
    try:
        return prune_stale_events(conn, ttl_hours=settings.event_ttl_hours)
    except Exception as exc:
        LOGGER.error("Event pruning failed: %s", exc)
        return {"pruned_events": 0, "reset_zones": 0, "error": str(exc)}


def _fire_scheduler_job(job_name: str) -> dict[str, object]:
    job = scheduler.get_job(job_name)
    if not job:
        return {"triggered": False, "reason": f"job_not_found:{job_name}"}
    scheduler.modify_job(job_name, next_run_time=datetime.now())
    return {"triggered": True, "job": job_name, "next_run_time": str(job.next_run_time)}


@app.on_event("startup")
def startup_event() -> None:
    is_live = getattr(settings, "data_mode", "demo") == "live"

    if is_live:
        scheduler.add_job(poll_firms, "interval", seconds=settings.poll_interval_firms, id="firms_poll", replace_existing=True)
        scheduler.add_job(poll_usgs, "interval", seconds=settings.poll_interval_usgs, id="usgs_poll", replace_existing=True)
        scheduler.add_job(poll_eonet, "interval", seconds=settings.poll_interval_eonet, id="eonet_poll", replace_existing=True)
        scheduler.add_job(poll_gdacs, "interval", seconds=settings.poll_interval_gdacs, id="gdacs_poll", replace_existing=True)
        scheduler.add_job(poll_open_meteo, "interval", seconds=settings.poll_interval_open_meteo, id="open_meteo_poll", replace_existing=True)
        scheduler.add_job(poll_air_quality, "interval", seconds=settings.poll_interval_air_quality, id="air_quality_poll", replace_existing=True)
        scheduler.add_job(poll_prune, "interval", seconds=settings.poll_interval_prune, id="prune_poll", replace_existing=True)
    else:
        scheduler.add_job(poll_firms, "interval", minutes=15, id="firms_poll", replace_existing=True)
        scheduler.add_job(poll_sentinel, "interval", hours=1, id="sentinel_poll", replace_existing=True)

    scheduler.start()


@app.on_event("shutdown")
def shutdown_event() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.post("/ingest/firms")
def ingest_firms_endpoint() -> dict[str, int]:
    try:
        return poll_firms()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/usgs")
def ingest_usgs_endpoint() -> dict[str, int]:
    try:
        return poll_usgs()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/eonet")
def ingest_eonet_endpoint() -> dict[str, int]:
    try:
        return poll_eonet()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/gdacs")
def ingest_gdacs_endpoint() -> dict[str, int]:
    try:
        return poll_gdacs()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/open_meteo")
def ingest_open_meteo_endpoint() -> dict[str, int]:
    try:
        return poll_open_meteo()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/air_quality")
def ingest_air_quality_endpoint() -> dict[str, int]:
    try:
        return poll_air_quality()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/sentinel")
def ingest_sentinel_endpoint() -> dict[str, int]:
    try:
        return poll_sentinel()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/osm_roads")
def ingest_osm_roads_endpoint() -> dict[str, int]:
    try:
        return ingest_osm_roads(_conn())
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/osm_resources")
def ingest_osm_resources_endpoint() -> dict[str, int]:
    try:
        return ingest_osm_resources(_conn())
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/prune")
def ingest_prune_endpoint() -> dict[str, int]:
    try:
        return poll_prune()
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/run_all")
def ingest_run_all() -> dict[str, object]:
    try:
        is_live = getattr(settings, "data_mode", "demo") == "live"
        if is_live:
            return {
                "firms": poll_firms(),
                "usgs": poll_usgs(),
                "eonet": poll_eonet(),
                "gdacs": poll_gdacs(),
                "open_meteo": poll_open_meteo(),
                "air_quality": poll_air_quality(),
                "osm_roads": ingest_osm_roads(_conn()),
                "prune": poll_prune(),
            }
        else:
            return {
                "firms": poll_firms(),
                "sentinel": poll_sentinel(),
                "osm_roads": ingest_osm_roads(_conn()),
            }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/scheduler/trigger")
def trigger_scheduler_jobs() -> dict[str, object]:
    try:
        is_live = getattr(settings, "data_mode", "demo") == "live"
        if is_live:
            jobs = [j.id for j in scheduler.get_jobs()]
            return {jid: _fire_scheduler_job(jid) for jid in jobs}
        return {
            "firms_poll": _fire_scheduler_job("firms_poll"),
            "sentinel_poll": _fire_scheduler_job("sentinel_poll"),
        }
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/ingest/health")
def ingest_health() -> dict[str, object]:
    return {
        "status": "ok",
        "scheduler_running": bool(getattr(scheduler, "running", False)),
        "firms_job": bool(scheduler.get_job("firms_poll")),
        "sentinel_job": bool(scheduler.get_job("sentinel_poll")),
    }
