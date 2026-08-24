#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

osascript - "$ROOT_DIR" <<'APPLESCRIPT'
on run argv
  set projectDir to item 1 of argv
  set backendDir to projectDir & "/backend-futures-py"

  tell application "iTerm"
    activate
    set mainWindow to (create window with default profile)
    delay 2

    tell current session of mainWindow
      write text "cd " & quoted form of backendDir
      write text "source .venv/bin/activate"
      write text "python monitor_and_trade_six_strategy.py"
    end tell
  end tell
end run
APPLESCRIPT
