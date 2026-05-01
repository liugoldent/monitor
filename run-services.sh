#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$SCRIPT_DIR/run-trade-services.sh"

osascript <<'EOF2'
tell application "iTerm"
  activate

  set mainWindow to (create window with default profile)
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-heyu-node"
    write text "source ~/.nvm/nvm.sh"
    write text "nvm use v22"
    write text "node index.js"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_mxf.py"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_stock_futures.py"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_pocket_etf.py"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cloudflared tunnel run --token eyJhIjoiZDM2YTZkNDkzNGEzMjI5ZjI1ZWQwZjQwOWE0OTU1MzEiLCJ0IjoiNzhiM2VlZjctNGNiMy00N2ZhLWEwOGUtMWMzZTU0MWQ2YjgyIiwicyI6Ik1qVTNaV0psTjJNdE0yRTFaQzAwTWpCakxUa3daR0l0TUdZeE1XVmtOVGc0TXpOayJ9"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python webhook_server.py"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/frontend-vue"
    write text "source ~/.nvm/nvm.sh"
    write text "nvm use v22"
    write text "pnpm dev"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_render_ping.py"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python mongo_market_api.py"
  end tell
  delay 2

end tell
EOF2
