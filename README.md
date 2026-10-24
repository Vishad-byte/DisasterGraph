# DisasterGraph

Real-time disaster emergency response orchestration using Neo4j (AuraDB Free / Cypher), FastAPI, Claude / OpenRouter, and Telegram.

## Stack

- Python 3.11+
- Neo4j AuraDB Free + official `neo4j` Python driver
- FastAPI + Uvicorn + APScheduler
- OpenRouter (Claude 3.5 Sonnet) or Anthropic Claude API
- python-telegram-bot
- NASA FIRMS + Sentinel-5P (cached for demo)
- OSM via osmnx

## Project Structure

```text
disastergraph/
  agent/
    disaster_agent.py
    prompts.py
    runner.py
  bot/
    bot.py
  dashboard/
    app.py
  graph/
    bootstrap.py
    demo_data.py
    queries.py
    schema.py
    seed_data.py
    utils.py
  ingestion/
    demo_scenario.py
    firms.py
    main.py
    osm_loader.py
    sentinel.py
requirements.txt
.env.example
.env
```

## Setup & Configuration

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Configure environment in `.env`:

```env
NEO4J_URI=neo4j+s://<your-instance-id>.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=<your-generated-password>

LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-openrouter-key
LLM_MODEL=anthropic/claude-3.5-sonnet

TELEGRAM_TOKEN=your-telegram-bot-token
INGESTION_SERVICE_URL=http://127.0.0.1:8001
```

> **Note**: AuraDB Free automatically pauses after ~3 days of inactivity. If connection fails, resume it from [console.neo4j.io](https://console.neo4j.io).

## Bootstrap Graph (Schema + Constraints + Seed Data)

To create schema constraints and seed 50 zones, 200 persons, resources, and routes:

```bash
python -m disastergraph.graph.bootstrap
```

Or run one-shot demo bootstrap with cached satellite data:

```bash
python -m disastergraph.demo_runner
```

## Running the Services

Open separate terminals in the project root:

### Terminal 1: Ingestion Service (Port 8001)
```bash
uvicorn disastergraph.ingestion.main:app --host 0.0.0.0 --port 8001 --reload
```

### Terminal 2: Dashboard Command Center API (Port 8002)
```bash
uvicorn disastergraph.dashboard.app:app --host 0.0.0.0 --port 8002 --reload
```

### Terminal 3: LLM Dispatch Agent Loop
```bash
python -m disastergraph.agent.disaster_agent
```

### Terminal 4: Telegram Alert Bot (Optional)
```bash
python -m disastergraph.bot.bot
```

---

## Frontend Web Command Center

Open your browser to:
- **`http://localhost:8002/ui`**

To rebuild frontend assets:
```bash
cd frontend
npm install
npm run build
```

---

## Verification & Testing

Run all unit and end-to-end tests:

```bash
# Run unit and parser tests (offline, no network required)
python -m unittest test_disastergraph_smoke.py
python -m unittest tests.test_live_ingestion

# Run integration tests against API
python -m unittest tests.integration.test_ingestion_api tests.integration.test_dashboard_api

# Run full live loop pipeline verification
python -m unittest tests.test_full_live_loop

# Run real-time live feed probe
python probe_live_feeds.py
```

---

## Live Data Mode

DisasterGraph supports a dual-mode engine configured via `DATA_MODE=live|demo` in `.env`.

- **`DATA_MODE=demo` (Default)**: Uses historical cached files and synthetic events (`data/cache/delhi_flood_2023.csv`, `firms.csv`, `sentinel_no2.csv`). Ideal for offline demonstrations, unit tests, and CI/CD pipelines.
- **`DATA_MODE=live`**: Connects to real-time, 100% free external feeds (NASA, USGS, GDACS, Open-Meteo, OpenAQ, OpenStreetMap) with APScheduler background polling, automated route blockage calculation, and TTL pruning to stay strictly within Neo4j AuraDB Free tier limits.

### Live Feeds Specification Matrix

| Source | Event Type | API Key Requirement | Registration Link | Default Poll Interval | Known Latency / Update Frequency |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **NASA FIRMS** | Wildfires, Industrial fires (`fire`) | **Free MAP Key** | [Register at NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/api/map_key/) | 15 min (`900s`) | 1–3 hours (MODIS / VIIRS orbit pass) |
| **USGS Earthquakes** | Earthquakes & Tremors (`earthquake`) | **None** (Open GeoJSON) | Public endpoint (no sign-up) | 1 min (`60s`) | 1–5 minutes post-detection |
| **NASA EONET v3** | Floods, Storms, Volcanoes, Wildfires | **None** (Open REST API) | [NASA EONET](https://eonet.gsfc.nasa.gov/) | 10 min (`600s`) | Real-time curated events |
| **GDACS** | Floods, Tropical Cyclones, Earthquakes | **None** (Open GeoJSON/RSS) | [GDACS Portal](https://www.gdacs.org/) | 10 min (`600s`) | Real-time alert broadcasts |
| **Open-Meteo** | Severe Rainfall, Flood Discharge, Wind | **None** (Open Source API) | [Open-Meteo](https://open-meteo.com/) | 30 min (`1800s`) | Hourly telemetry & forecasts |
| **OpenAQ v3 / Open-Meteo AQ** | Air Quality, Gas plumes (`pollution`) | **Optional Key** (`OPENAQ_API_KEY`) / None for Open-Meteo fallback | [OpenAQ Signup](https://explore.openaq.org/register) | 15 min (`900s`) | 15–60 minutes |
| **OpenStreetMap via OSMnx** | Road Network & Emergency Facilities | **None** (Overpass API with local cache) | OpenStreetMap | On Bootstrap & reload | Static / Cached to disk |

### What is Live vs. What is Synthetic

| Entity / Property | Mode in Live Operation | Reason & Compliance |
| :--- | :--- | :--- |
| **Disaster Events (`DisasterEvent`)** | **100% Live** | Real-time observations from NASA FIRMS, USGS, GDACS, EONET, Open-Meteo. |
| **Road Network (`Route`, `CONNECTS`)** | **100% Live** | OpenStreetMap drivable topological graph for the configured city. |
| **Hospitals, Fire, Police (`Resource`)** | **100% Live** (Locations & Names) | Real geographic coordinates and facility names queried from OpenStreetMap. |
| **Resource Bed/Unit Capacities** | **Estimated / Synthetic fallback** | Most OSM facility tags omit live bed counts; marked with `capacity_source: "estimated"`. |
| **Citizens / Patients (`Person`)** | **Synthetic** (`source: "synthetic"`) | **Privacy & DPDP/GDPR Compliance**: Real patient names, medical histories, and home coordinates cannot be published. |
| **Ambulance Live GPS** | **Synthetic** (`source: "synthetic"`) | Emergency CAD radio feeds are private to municipal 911/112 command centers; ambulances are stationed at real OSM hospitals. |

### AuraDB Free Safeguards & TTL Pruning

Neo4j AuraDB Free enforces a 200,000 node / 400,000 relationship cap. To ensure DisasterGraph can run indefinitely at zero cost:
- **`EVENT_TTL_HOURS`** (default `24`): Background pruner job runs every hour to remove or expire `DisasterEvent` nodes older than 24 hours.
- Orphaned relationship cleanup: Automatically recalculates affected zones and resets `is_affected = false` when all events in that zone expire.
- Disk caching: OSM road graphs and facility data are cached in `data/cache/` to minimize external HTTP overhead.

