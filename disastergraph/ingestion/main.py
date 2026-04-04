from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException

from disastergraph.config import get_settings, get_tg_connection
from disastergraph.ingestion.firms import fetch_firms_csv, ingest_firms_from_csv
from disastergraph.ingestion.osm_loader import ingest_osm_roads
from disastergraph.ingestion.sentinel import ingest_sentinel_cached


app = FastAPI(title="DisasterGraph Ingestion Service", version="0.1.0")
settings = get_settings()
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")


def _conn():
    return get_tg_connection(settings)


def poll_firms() -> dict[str, int]:
    conn = _conn()
    if settings.firms_map_key:
        csv_text = fetch_firms_csv(settings.firms_map_key)
    else:
        with open(settings.firms_cache_path, "r", encoding="utf-8") as fp:
            csv_text = fp.read()
    return ingest_firms_from_csv(conn, csv_text)


def poll_sentinel() -> dict[str, int]:
    conn = _conn()
    return ingest_sentinel_cached(conn, settings.sentinel_cache_path)


def _fire_scheduler_job(job_name: str) -> dict[str, object]:
    job = scheduler.get_job(job_name)
    if not job:
        return {"triggered": False, "reason": f"job_not_found:{job_name}"}
    scheduler.modify_job(job_name, next_run_time=datetime.now())
    return {"triggered": True, "job": job_name, "next_run_time": str(job.next_run_time)}


@app.on_event("startup")
def startup_event() -> None:
    scheduler.add_job(poll_firms, "interval", minutes=15, id="firms_poll", replace_existing=True)
    scheduler.add_job(
        poll_sentinel,
        "interval",
        hours=1,
        id="sentinel_poll",
        replace_existing=True,
    )
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


@app.post("/ingest/run_all")
def ingest_run_all() -> dict[str, object]:
    try:
        firms = poll_firms()
        sentinel = poll_sentinel()
        roads = ingest_osm_roads(_conn())
        return {"firms": firms, "sentinel": sentinel, "osm_roads": roads}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ingest/scheduler/trigger")
def trigger_scheduler_jobs() -> dict[str, object]:
    try:
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
