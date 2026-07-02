#!/bin/bash

osascript <<'EOF2'
tell application "iTerm"
  activate

  set mainWindow to (create window with default profile)
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_and_trade_six_strategy.py"
  end tell
  delay 2

  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/shioaji_demo_shane"
    write text "source .venv/bin/activate"
    write text "python monitor_and_trade.py"
  end tell
  delay 2

end tell
EOF2
