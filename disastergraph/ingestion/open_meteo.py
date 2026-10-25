from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from disastergraph.graph.utils import ZoneInfo, list_zones

LOGGER = logging.getLogger(__name__)

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(slots=True)
class MeteoObservation:
    zone_id: str
    latitude: float
    longitude: float
    precipitation_mm: float
    rain_mm: float
    wind_speed_kmh: float
    time_iso: str
    severity: float
    event_type: str


def fetch_open_meteo_for_zones(
    zones: list[ZoneInfo],
    timeout: float = 25.0,
    retries: int = 2,
) -> list[dict[str, Any]]:
    if not zones:
        return []

    # Chunk into batches of up to 40 locations per request
    batch_size = 40
    all_results: list[dict[str, Any]] = []

    for i in range(0, len(zones), batch_size):
        chunk = zones[i : i + batch_size]
        lats_str = ",".join(f"{z.centroid_lat:.4f}" for z in chunk)
        lngs_str = ",".join(f"{z.centroid_lng:.4f}" for z in chunk)

        params = {
            "latitude": lats_str,
            "longitude": lngs_str,
            "current": "precipitation,rain,wind_speed_10m",
        }

        last_err: Exception | None = None
        for attempt in range(max(1, retries + 1)):
            try:
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(OPEN_METEO_FORECAST_URL, params=params)
                    resp.raise_for_status()
                    payload = resp.json()
                    if isinstance(payload, list):
                        all_results.extend(payload)
                    elif isinstance(payload, dict):
                        all_results.append(payload)
                    last_err = None
                    break
            except Exception as exc:
                last_err = exc
                LOGGER.warning("Open-Meteo batch attempt %d failed: %s", attempt + 1, exc)

        if last_err is not None:
            LOGGER.error("Failed to fetch Open-Meteo batch: %s", last_err)

    return all_results


def parse_meteo_responses(
    responses: list[dict[str, Any]],
    zones: list[ZoneInfo],
) -> list[MeteoObservation]:
    observations: list[MeteoObservation] = []
    zone_by_idx = {idx: zone for idx, zone in enumerate(zones)}

    for idx, resp in enumerate(responses):
        if not isinstance(resp, dict):
            continue
        current = resp.get("current", {}) or {}
        precip = float(current.get("precipitation") or 0.0)
        rain = float(current.get("rain") or 0.0)
        wind = float(current.get("wind_speed_10m") or 0.0)
        t_str = str(current.get("time") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M"))

        zone = zone_by_idx.get(idx)
        if not zone:
            continue

        rain_sev = min(1.0, max(precip, rain) / 35.0)
        wind_sev = min(1.0, wind / 75.0)
        total_sev = round(max(rain_sev, wind_sev), 3)

        event_type = "flood" if rain_sev >= wind_sev else "storm"

        observations.append(
            MeteoObservation(
                zone_id=zone.zone_id,
                latitude=zone.centroid_lat,
                longitude=zone.centroid_lng,
                precipitation_mm=precip,
                rain_mm=rain,
                wind_speed_kmh=wind,
                time_iso=t_str,
                severity=total_sev,
                event_type=event_type,
            )
        )
    return observations


def ingest_open_meteo_data(
    conn: Any,
    responses: list[dict[str, Any]],
    zones: list[ZoneInfo] | None = None,
    source: str = "Open-Meteo",
    severity_threshold: float = 0.35,
) -> dict[str, int]:
    if zones is None:
        zones = list_zones(conn)
    if not zones:
        return {"events": 0, "writes": 0}

    obs_list = parse_meteo_responses(responses, zones)
    writes = 0
    events_count = 0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for obs in obs_list:
        if obs.severity < severity_threshold:
            continue

        # Format date bucket for stable deduplication (hourly)
        hour_bucket = obs.time_iso.replace(":", "").replace("-", "")[:11]
        event_id = f"meteo_{obs.zone_id}_{hour_bucket}"

        writes += conn.upsertVertex(
            "DisasterEvent",
            event_id,
            {
                "event_type": obs.event_type,
                "type": obs.event_type,
                "timestamp": obs.time_iso,
                "ts": obs.time_iso,
                "severity": obs.severity,
                "precipitation_mm": obs.precipitation_mm,
                "rain_mm": obs.rain_mm,
                "wind_speed_kmh": obs.wind_speed_kmh,
                "lat": obs.latitude,
                "lon": obs.longitude,
                "latitude": obs.latitude,
                "longitude": obs.longitude,
                "satellite_source": source,
                "source": source,
                "status": "active",
                "ingested_at": now_utc,
            },
        )
        writes += conn.upsertEdge(
            "DisasterEvent",
            event_id,
            "affects",
            "Zone",
            obs.zone_id,
            {"severity": obs.severity},
        )
        writes += conn.upsertVertex(
            "Zone",
            obs.zone_id,
            {"is_affected": True, "disaster_severity": obs.severity},
        )
        events_count += 1

    return {"events": events_count, "writes": writes}
