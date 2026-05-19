#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATE_ARG="${1:-$(TZ=Asia/Taipei date +%F)}"
NODE_BIN="/Users/kt/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin"

cd "$ROOT_DIR/backend-futures-py"
.venv/bin/python fetch_stock_tech.py \
  --min-count "${ETF_MIN_COUNT:-3}" \
  --report-date "$DATE_ARG" \
  --output-date "$DATE_ARG" \
  --sleep "${YAHOO_SLEEP:-0.2}"

cd "$ROOT_DIR/frontend-vue"
PATH="$NODE_BIN:$PATH" pnpm institutional:normalize "$DATE_ARG"
