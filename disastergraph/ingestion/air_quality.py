from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from disastergraph.graph.utils import ZoneInfo, list_zones, nearest_zone

LOGGER = logging.getLogger(__name__)

OPENAQ_LOCATIONS_URL = "https://api.openaq.org/v3/locations"
OPEN_METEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


@dataclass(slots=True)
class AirQualityReading:
    zone_id: str
    latitude: float
    longitude: float
    no2: float
    pm2_5: float
    aqi: float
    severity: float
    source: str
    time_iso: str


def fetch_openaq_readings(
    api_key: str,
    bbox: tuple[float, float, float, float],
    zones: list[ZoneInfo],
    timeout: float = 20.0,
) -> list[AirQualityReading]:
    if not api_key:
        return []

    south, west, north, east = bbox
    # OpenAQ bbox format: minLon,minLat,maxLon,maxLat
    bbox_str = f"{west},{south},{east},{north}"
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    params = {"bbox": bbox_str, "limit": 50}

    readings: list[AirQualityReading] = []
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(OPENAQ_LOCATIONS_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", []) if isinstance(data, dict) else []

            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            for item in results:
                if not isinstance(item, dict):
                    continue
                coords = item.get("coordinates", {}) or {}
                lat = coords.get("latitude")
                lng = coords.get("longitude")
                if lat is None or lng is None:
                    continue

                zone = nearest_zone(float(lat), float(lng), zones)
                # Parse sensors
                sensors = item.get("sensors", []) or []
                no2_val = 0.0
                pm25_val = 0.0
                for s in sensors:
                    param = s.get("parameter", {}) if isinstance(s, dict) else {}
                    p_name = param.get("name", "").lower()
                    val = float(s.get("latest", {}).get("value") or 0.0) if isinstance(s.get("latest"), dict) else 0.0
                    if "no2" in p_name:
                        no2_val = max(no2_val, val)
                    elif "pm25" in p_name or "pm2.5" in p_name:
                        pm25_val = max(pm25_val, val)

                sev = round(min(1.0, max(no2_val / 150.0, pm25_val / 200.0)), 3)
                readings.append(
                    AirQualityReading(
                        zone_id=zone.zone_id,
                        latitude=float(lat),
                        longitude=float(lng),
                        no2=no2_val,
                        pm2_5=pm25_val,
                        aqi=sev * 100.0,
                        severity=sev,
                        source="OpenAQ-v3",
                        time_iso=now_utc,
                    )
                )
    except Exception as exc:
        LOGGER.warning("OpenAQ v3 fetch failed, falling back: %s", exc)

    return readings


def fetch_open_meteo_aq_for_zones(
    zones: list[ZoneInfo],
    timeout: float = 20.0,
) -> list[AirQualityReading]:
    if not zones:
        return []

    # Query up to 30 zone centroids
    sample_zones = zones[:30]
    lats = ",".join(f"{z.centroid_lat:.4f}" for z in sample_zones)
    lngs = ",".join(f"{z.centroid_lng:.4f}" for z in sample_zones)

    params = {
        "latitude": lats,
        "longitude": lngs,
        "current": "pm2_5,nitrogen_dioxide,sulphur_dioxide,european_aqi",
    }

    readings: list[AirQualityReading] = []
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(OPEN_METEO_AQ_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            items = data if isinstance(data, list) else [data]

            for idx, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                current = item.get("current", {}) or {}
                no2 = float(current.get("nitrogen_dioxide") or 0.0)
                pm25 = float(current.get("pm2_5") or 0.0)
                aqi = float(current.get("european_aqi") or 0.0)

                zone = sample_zones[idx] if idx < len(sample_zones) else sample_zones[0]
                sev = round(min(1.0, max(no2 / 160.0, pm25 / 160.0, aqi / 160.0)), 3)

                readings.append(
                    AirQualityReading(
                        zone_id=zone.zone_id,
                        latitude=zone.centroid_lat,
                        longitude=zone.centroid_lng,
                        no2=no2,
                        pm2_5=pm25,
                        aqi=aqi,
                        severity=sev,
                        source="Open-Meteo-AQ",
                        time_iso=now_utc,
                    )
                )
    except Exception as exc:
        LOGGER.warning("Open-Meteo AQ fetch failed: %s", exc)

    return readings


def apply_route_penalties_for_gas_plumes(
    conn: Any,
    zone_id: str,
    severity: float,
) -> int:
    """Apply graph route penalties and speed reductions for hazardous gas / pollution plumes (avoid, not impassable)."""
    if severity < 0.5:
        return 0

    reason = "High NO2 / PM2.5 pollution nearby: speed reduced & transit delay applied"

    # Roads affected by pollution stay passable (is_blocked = false), flagged as 'avoid' with transit time penalty
    cypher_edges = """
    MATCH (z:Zone {zone_id: $zid})-[c:CONNECTS]-(target:Zone)
    SET c.is_blocked = false,
        c.status = 'avoid',
        c.blockage_reason = $reason,
        c.estimated_time_min = round(c.estimated_time_min * (1.0 + $severity * 0.5), 2)
    RETURN count(c) AS cnt
    """
    res = conn.run_query(
        cypher_edges,
        {"zid": zone_id, "reason": reason, "severity": severity},
    )
    edge_cnt = int(res[0]["cnt"]) if res else 0

    # 2. Update Route vertex
    cypher_routes = """
    MATCH (rt:Route) WHERE rt.start_zone = $zid OR rt.end_zone = $zid
    SET rt.is_blocked = false,
        rt.status = 'avoid',
        rt.blockage_reason = $reason,
        rt.estimated_time_min = round(rt.estimated_time_min * (1.0 + $severity * 0.5), 2)
    RETURN count(rt) AS cnt
    """
    conn.run_query(
        cypher_routes,
        {"zid": zone_id, "reason": reason, "severity": severity},
    )

    return edge_cnt


def ingest_air_quality_readings(
    conn: Any,
    readings: list[AirQualityReading],
    severity_threshold: float = 0.5,
) -> dict[str, int]:
    writes = 0
    events_count = 0
    penalties_applied = 0
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    for r in readings:
        if r.severity < severity_threshold:
            continue

        hour_bucket = r.time_iso.replace(":", "").replace("-", "").replace(" ", "T")[:13]
        event_id = f"aq_{r.zone_id}_{hour_bucket}"

        writes += conn.upsertVertex(
            "DisasterEvent",
            event_id,
            {
                "event_type": "pollution",
                "type": "pollution",
                "timestamp": r.time_iso,
                "ts": r.time_iso,
                "severity": r.severity,
                "no2": r.no2,
                "pm2_5": r.pm2_5,
                "aqi": r.aqi,
                "lat": r.latitude,
                "lon": r.longitude,
                "latitude": r.latitude,
                "longitude": r.longitude,
                "satellite_source": r.source,
                "source": r.source,
                "status": "active",
                "ingested_at": now_utc,
            },
        )
        writes += conn.upsertEdge(
            "DisasterEvent",
            event_id,
            "affects",
            "Zone",
            r.zone_id,
            {"severity": r.severity},
        )
        writes += conn.upsertVertex(
            "Zone",
            r.zone_id,
            {"is_affected": True, "disaster_severity": r.severity},
        )
        events_count += 1

        # Feed gas plume severity into route penalties
        penalties_applied += apply_route_penalties_for_gas_plumes(conn, r.zone_id, r.severity)

    return {"events": events_count, "writes": writes, "penalties_applied": penalties_applied}
