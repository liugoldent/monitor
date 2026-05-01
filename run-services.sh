#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$SCRIPT_DIR/run-trade-services.sh"

osascript <<'EOF2'
tell application "iTerm2"
  activate

  create window with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/backend-heyu-node && source ~/.nvm/nvm.sh && nvm use v22 && node index.js; exec zsh -l'"
  delay 2

  tell current window
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/backend-futures-py && source .venv/bin/activate && python monitor_mxf.py; exec zsh -l'"
    delay 2
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/backend-futures-py && source .venv/bin/activate && python monitor_stock_futures.py; exec zsh -l'"
    delay 2
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/backend-futures-py && source .venv/bin/activate && python monitor_pocket_etf.py; exec zsh -l'"
    delay 2
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cloudflared tunnel run --token eyJhIjoiZDM2YTZkNDkzNGEzMjI5ZjI1ZWQwZjQwOWE0OTU1MzEiLCJ0IjoiNzhiM2VlZjctNGNiMy00N2ZhLWEwOGUtMWMzZTU0MWQ2YjgyIiwicyI6Ik1qVTNaV0psTjJNdE0yRTFaQzAwTWpCakxUa3daR0l0TUdZeE1XVmtOVGc0TXpOayJ9; exec zsh -l'"
    delay 2
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/backend-futures-py && source .venv/bin/activate && python webhook_server.py; exec zsh -l'"
    delay 2
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/frontend-vue && source ~/.nvm/nvm.sh && nvm use v22 && pnpm dev; exec zsh -l'"
    delay 2
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/backend-futures-py && source .venv/bin/activate && python monitor_render_ping.py; exec zsh -l'"
    delay 2
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/backend-futures-py && source .venv/bin/activate && python mongo_market_api.py; exec zsh -l'"
  end tell
  delay 2
end tell
EOF2
