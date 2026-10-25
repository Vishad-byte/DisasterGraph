#!/usr/bin/env bash
set -e

echo "=== Starting DisasterGraph Services ==="

# 1. Start Ingestion Service on internal port 8001
echo "Starting Ingestion Service on port 8001..."
uvicorn disastergraph.ingestion.main:app --host 127.0.0.1 --port 8001 &

# 2. Optionally start Telegram Bot if token provided and enabled
if [ "$ENABLE_BOT" = "true" ] && [ -n "$TELEGRAM_TOKEN" ]; then
    echo "Starting Telegram Alert Bot in background..."
    python -m disastergraph.bot.bot &
fi

# 3. Optionally start LLM Agent daemon loop
if [ "$ENABLE_AGENT_DAEMON" = "true" ]; then
    echo "Starting LLM Dispatch Agent loop in background..."
    python -m disastergraph.agent.disaster_agent &
fi

# 4. Start Dashboard and UI server on Render's assigned external port
PORT="${PORT:-8002}"
echo "Starting Dashboard API and Web UI on port $PORT..."
exec uvicorn disastergraph.dashboard.app:app --host 0.0.0.0 --port "$PORT"
