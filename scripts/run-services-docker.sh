#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
export TZ="${TZ:-Asia/Taipei}"

pids=()
names=()

should_start() {
  local name="$1"
  if [[ -z "${SERVICE_FILTER:-}" ]]; then
    return 0
  fi
  [[ ",${SERVICE_FILTER}," == *",${name},"* ]]
}

start_service() {
  local name="$1"
  local workdir="$2"
  shift 2

  if ! should_start "$name"; then
    echo "⏭️  skip $name because SERVICE_FILTER=${SERVICE_FILTER}"
    return 0
  fi

  echo "🚀 start $name"
  (
    cd "$ROOT_DIR/$workdir"
    exec "$@"
  ) &
  pids+=("$!")
  names+=("$name")
}

stop_services() {
  echo "🛑 stopping services"
  for pid in "${pids[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}

trap stop_services INT TERM EXIT

start_service "trade-main" "backend-futures-py" python monitor_and_trade.py
start_service "trade-shane" "shioaji_demo_shane" python monitor_and_trade.py
start_service "heyu-node" "backend-heyu-node" node index.js
start_service "monitor-mxf" "backend-futures-py" python monitor_mxf.py
start_service "monitor-stock-futures" "backend-futures-py" python monitor_stock_futures.py
start_service "monitor-pocket-etf" "backend-futures-py" python monitor_pocket_etf.py
start_service "webhook-server" "backend-futures-py" python webhook_server.py
start_service "frontend-vue" "frontend-vue" pnpm dev --host 0.0.0.0
start_service "monitor-render-ping" "backend-futures-py" python monitor_render_ping.py
start_service "mongo-market-api" "backend-futures-py" python mongo_market_api.py
start_service "google-clockin" "google-clockin" python hqt_keedem_schedule_output.py

if should_start "cloudflared"; then
  if [[ -n "${CLOUDFLARED_TOKEN:-}" ]]; then
    start_service "cloudflared" "." cloudflared tunnel run --token "$CLOUDFLARED_TOKEN"
  else
    echo "⚠️  skip cloudflared: CLOUDFLARED_TOKEN is empty"
  fi
fi

echo "✅ all requested services started"
wait -n "${pids[@]}"
exit_code="$?"
echo "❌ a service exited; shutting down container (exit=$exit_code)"
exit "$exit_code"
