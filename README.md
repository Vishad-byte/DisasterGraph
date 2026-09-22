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
# Run 3 core migration verification scenarios
python e2e_verification.py

# Run all test suites
python -m unittest test_disastergraph_smoke.py
python -m unittest discover -s tests/integration -p "test_*.py"
```
