#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ "${RUN_SERVICES_DOCKER:-}" == "1" || -f /.dockerenv ]]; then
  exec bash "$SCRIPT_DIR/scripts/run-services-docker.sh"
fi

bash "$SCRIPT_DIR/run-trade-services.sh"

osascript <<'EOF2'
tell application "iTerm"
  activate

  set cloudflaredToken to system attribute "CLOUDFLARED_TOKEN"

  set mainWindow to (create window with default profile)
  delay 2

  # tell current session of mainWindow
  #   write text "cd ~/Desktop/self/monitor/backend-heyu-node"
  #   write text "source ~/.nvm/nvm.sh"
  #   write text "nvm use v22"
  #   write text "node index.js"
  # end tell
  # delay 2

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

  # tell mainWindow
  #   create tab with default profile
  # end tell
  # delay 2

  # tell current session of mainWindow
  #   write text "cd ~/Desktop/self/monitor/backend-futures-py"
  #   write text "source .venv/bin/activate"
  #   write text "python monitor_stock_futures.py"
  # end tell
  # delay 2

  # tell mainWindow
  #   create tab with default profile
  # end tell
  # delay 2

  # tell current session of mainWindow
  #   write text "cd ~/Desktop/self/monitor/backend-futures-py"
  #   write text "source .venv/bin/activate"
  #   write text "python monitor_pocket_etf.py"
  # end tell
  # delay 2

  if cloudflaredToken is not "" then
    tell mainWindow
      create tab with default profile
    end tell
    delay 2

    tell current session of mainWindow
      write text "cloudflared tunnel run --token " & quoted form of cloudflaredToken
    end tell
    delay 2
  end if

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

  # tell mainWindow
  #   create tab with default profile
  # end tell
  # delay 2

  # tell current session of mainWindow
  #   write text "cd ~/Desktop/self/monitor/frontend-vue"
  #   write text "source ~/.nvm/nvm.sh"
  #   write text "nvm use v22"
  #   write text "pnpm dev"
  # end tell
  # delay 2

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

  # tell mainWindow
  #   create tab with default profile
  # end tell
  # delay 2

  # tell current session of mainWindow
  #   write text "cd ~/Desktop/self/monitor/backend-futures-py"
  #   write text "source .venv/bin/activate"
  #   write text "python mongo_market_api.py"
  # end tell
  # delay 2

end tell
EOF2
