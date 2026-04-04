# DisasterGraph

Real-time disaster emergency response orchestration using TigerGraph, FastAPI, Claude, and Telegram.

## Stack

- Python 3.11+
- TigerGraph Savanna + pyTigerGraph
- FastAPI + APScheduler
- Anthropic Claude API
- OpenRouter (default) or Anthropic Claude API
- python-telegram-bot
- NASA FIRMS + Sentinel-5P (cached for demo)
- OSM via osmnx

## Project Structure

```text
disastergraph/
  agent/
    disaster_agent.py
    prompts.py
  bot/
    bot.py
  dashboard/
    app.py
  graph/
    bootstrap.py
    demo_data.py
    queries.gsql
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
```

## Setup

1. Create virtual environment and install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill credentials.

   LLM options:

   - Default: OpenRouter (`LLM_PROVIDER=openrouter` + `OPENROUTER_API_KEY`)
   - Optional: direct Anthropic (`LLM_PROVIDER=claude` + `CLAUDE_API_KEY`)

3. Prepare demo cache files (optional but recommended for hackathon demo):
   - `data/cache/firms.csv`
   - `data/cache/sentinel_no2.csv`
   - `data/cache/delhi_flood_2023.csv`
   - `data/cache/delhi_wards.geojson` (optional)

## Bootstrap Graph (Phase 1 + 2 + seed)

```bash
python -m disastergraph.graph.bootstrap
```

One-shot demo bootstrap + cached ingestion:

```bash
python -m disastergraph.demo_runner
```

What this does:

- Creates graph schema in TigerGraph
- Installs all GSQL queries
- Seeds zones/persons/resources/routes/events

## Run Services

In separate terminals:

```bash
uvicorn disastergraph.ingestion.main:app --host 0.0.0.0 --port 8001 --reload
uvicorn disastergraph.dashboard.app:app --host 0.0.0.0 --port 8002 --reload
python -m disastergraph.bot.bot
python -m disastergraph.agent.disaster_agent
```

## API Endpoints

### Ingestion

- `POST /ingest/firms`
- `POST /ingest/sentinel`
- `POST /ingest/osm_roads`
- `POST /ingest/run_all`
- `POST /ingest/scheduler/trigger`
- `GET /ingest/health`

### Dashboard

- `GET /graph/snapshot`
- `GET /events/active`
- `GET /assignments/live`
- `GET /health`

## Telegram Commands

- `/register <officer_id> <zone_id> [name]`
- `/status <zone_id_or_zone_name>`

## Demo Flow (Phase 7)

1. Bootstrap graph.
2. Run ingestion service using cached data.
3. Trigger ingestion:

```bash
curl -X POST http://localhost:8001/ingest/run_all
```

4. Run agent loop and show query outputs + Claude assignment JSON.
5. Receive Telegram alert on officer account.
6. Open TigerGraph GraphStudio and show updated `assigned_to` edges.

## Notes

- Keep `.env` out of version control.
- Cached demo files are recommended for stable stage demos.
- GitHub Copilot is not exposed as a runtime API for this backend flow; use OpenRouter key for agent inference.
