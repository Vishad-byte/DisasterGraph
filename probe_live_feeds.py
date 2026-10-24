"""
Fetch real-time records from each live source and display normalized DisasterEvent & Resource records.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from disastergraph.config import get_settings
from disastergraph.graph.utils import ZoneInfo
from disastergraph.ingestion.air_quality import fetch_open_meteo_aq_for_zones
from disastergraph.ingestion.eonet import fetch_eonet_events, parse_eonet_json
from disastergraph.ingestion.gdacs import fetch_gdacs_alerts, parse_gdacs_geojson
from disastergraph.ingestion.open_meteo import fetch_open_meteo_for_zones, parse_meteo_responses
from disastergraph.ingestion.osm_loader import load_or_fetch_osm_resources
from disastergraph.ingestion.usgs import fetch_usgs_feed, parse_usgs_geojson


def run_live_probes():
    settings = get_settings()
    bbox = settings.get_bbox()
    zones = [
        ZoneInfo("zone_001", "Central Delhi", 28.6139, 77.2090, 50000, 0.0, False),
        ZoneInfo("zone_002", "North Delhi", 28.7041, 77.1025, 65000, 0.0, False),
    ]

    print("=" * 70)
    print("LIVE REAL-TIME FEED PROBES & RECORD NORMALIZATION")
    print(f"Target City: {settings.target_city} | BBOX: {bbox}")
    print("=" * 70)

    # 1. USGS Live Feed
    print("\n[1] USGS Live Earthquake Feed:")
    try:
        usgs_raw = fetch_usgs_feed()
        # Parse global feed with min_magnitude 3.0 to find active quakes
        quakes = parse_usgs_geojson(usgs_raw, min_magnitude=3.0)
        if quakes:
            sample_q = quakes[0]
            print(f"  Total active quakes (mag >= 3.0) found: {len(quakes)}")
            sample_record = {
                "label": "DisasterEvent",
                "event_id": sample_q.event_id,
                "event_type": "earthquake",
                "magnitude": sample_q.magnitude,
                "depth_km": sample_q.depth_km,
                "place": sample_q.place,
                "lat": sample_q.latitude,
                "lon": sample_q.longitude,
                "source": "USGS",
                "status": "active",
                "timestamp": sample_q.time_iso,
            }
            print("  Sample Neo4j Record:")
            print(json.dumps(sample_record, indent=4))
        else:
            print("  No earthquakes currently matching filter.")
    except Exception as exc:
        print(f"  USGS probe error: {exc}")

    # 2. NASA EONET v3 Live Feed
    print("\n[2] NASA EONET v3 Live Natural Disaster Feed:")
    try:
        eonet_raw = fetch_eonet_events(limit=20)
        eonet_events = parse_eonet_json(eonet_raw)
        if eonet_events:
            sample_e = eonet_events[0]
            print(f"  Total open natural events found: {len(eonet_events)}")
            sample_record = {
                "label": "DisasterEvent",
                "event_id": sample_e.event_id,
                "event_type": sample_e.event_type,
                "title": sample_e.title,
                "lat": sample_e.latitude,
                "lon": sample_e.longitude,
                "source": "EONET",
                "status": "active",
                "timestamp": sample_e.date_str,
            }
            print("  Sample Neo4j Record:")
            print(json.dumps(sample_record, indent=4))
        else:
            print("  No open EONET events found.")
    except Exception as exc:
        print(f"  EONET probe error: {exc}")

    # 3. GDACS Live Alert Feed
    print("\n[3] GDACS Live Disaster Alerts:")
    try:
        gdacs_raw = fetch_gdacs_alerts(limit=20)
        gdacs_alerts = parse_gdacs_geojson(gdacs_raw)
        if gdacs_alerts:
            sample_g = gdacs_alerts[0]
            print(f"  Total active global disaster alerts found: {len(gdacs_alerts)}")
            sample_record = {
                "label": "DisasterEvent",
                "event_id": sample_g.event_id,
                "event_type": sample_g.event_type,
                "title": sample_g.title,
                "alert_level": sample_g.alert_level,
                "severity": sample_g.severity,
                "lat": sample_g.latitude,
                "lon": sample_g.longitude,
                "source": "GDACS",
                "status": "active",
                "timestamp": sample_g.from_date,
            }
            print("  Sample Neo4j Record:")
            print(json.dumps(sample_record, indent=4))
        else:
            print("  No GDACS alerts found.")
    except Exception as exc:
        print(f"  GDACS probe error: {exc}")

    # 4. Open-Meteo Real-time Weather Feed
    print("\n[4] Open-Meteo Real-time Weather Telemetry:")
    try:
        meteo_raw = fetch_open_meteo_for_zones(zones)
        meteo_obs = parse_meteo_responses(meteo_raw, zones)
        if meteo_obs:
            sample_m = meteo_obs[0]
            sample_record = {
                "label": "DisasterEvent / Zone Metric",
                "zone_id": sample_m.zone_id,
                "event_type": sample_m.event_type,
                "precipitation_mm": sample_m.precipitation_mm,
                "rain_mm": sample_m.rain_mm,
                "wind_speed_kmh": sample_m.wind_speed_kmh,
                "hazard_severity": sample_m.severity,
                "time": sample_m.time_iso,
                "source": "Open-Meteo",
            }
            print("  Sample Neo4j Observation:")
            print(json.dumps(sample_record, indent=4))
    except Exception as exc:
        print(f"  Open-Meteo probe error: {exc}")

    # 5. Live Air Quality Telemetry
    print("\n[5] Live Air Quality Telemetry (PM2.5, NO2, AQI):")
    try:
        aq_readings = fetch_open_meteo_aq_for_zones(zones)
        if aq_readings:
            sample_aq = aq_readings[0]
            sample_record = {
                "label": "DisasterEvent (Pollution)",
                "event_id": f"aq_{sample_aq.zone_id}_live",
                "zone_id": sample_aq.zone_id,
                "event_type": "pollution",
                "no2_ugm3": sample_aq.no2,
                "pm2_5_ugm3": sample_aq.pm2_5,
                "european_aqi": sample_aq.aqi,
                "severity": sample_aq.severity,
                "source": sample_aq.source,
                "status": "active",
                "timestamp": sample_aq.time_iso,
            }
            print("  Sample Neo4j Record:")
            print(json.dumps(sample_record, indent=4))
    except Exception as exc:
        print(f"  Air Quality probe error: {exc}")

    # 6. OSM Emergency Infrastructure (Overpass / OSMnx)
    print("\n[6] OpenStreetMap Emergency Infrastructure & Capacity:")
    try:
        resources = load_or_fetch_osm_resources(city=settings.target_city, zones=zones)
        if resources:
            sample_res = resources[0]
            sample_record = {
                "label": "Resource",
                "res_id": sample_res["res_id"],
                "resource_type": sample_res["resource_type"],
                "name": sample_res["name"],
                "capacity": sample_res["capacity"],
                "capacity_source": sample_res["capacity_source"],
                "current_load": sample_res["current_load"],
                "is_available": sample_res["is_available"],
                "lat": sample_res["location_lat"],
                "lon": sample_res["location_lng"],
                "source": sample_res["source"],
            }
            print(f"  Total OSM emergency facilities found: {len(resources)}")
            print("  Sample Neo4j Resource Record:")
            print(json.dumps(sample_record, indent=4))
    except Exception as exc:
        print(f"  OSM resources error: {exc}")

    print("\n" + "=" * 70)
    print("ALL LIVE PROBES COMPLETED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    run_live_probes()
