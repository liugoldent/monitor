#!/bin/bash

osascript <<'EOF2'
tell application "iTerm2"
  activate

  create window with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/backend-futures-py && source .venv/bin/activate && python monitor_and_trade.py; exec zsh -l'"
  delay 2

  tell current window
    create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/shioaji-demo-shane && source .venv/bin/activate && python monitor_and_trade.py; exec zsh -l'"
  end tell
  delay 2

  # tell current window
  #   create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/shioaji-demo-rosco && source .venv/bin/activate && python monitor_and_trade.py; exec zsh -l'"
  # end tell
  # delay 2

  # tell current window
  #   create tab with default profile command "zsh -lc 'trap \"exec zsh -l\" INT TERM; cd ~/Desktop/self/monitor/shioaji-demo-ichih && source .venv/bin/activate && python monitor_and_trade.py; exec zsh -l'"
  # end tell
  # delay 2

  delay 2
end tell
EOF2
