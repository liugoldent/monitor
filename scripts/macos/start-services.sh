#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Keep secrets outside the script. An exported value takes precedence over
# the root .env file used by Docker Compose.
cloudflared_token="${CLOUDFLARED_TOKEN:-}"
if [[ -z "$cloudflared_token" && -f "$ROOT_DIR/.env" ]]; then
  while IFS='=' read -r key value; do
    if [[ "${key//[[:space:]]/}" == "CLOUDFLARED_TOKEN" ]]; then
      cloudflared_token="${value%$'\r'}"
      cloudflared_token="${cloudflared_token#\"}"
      cloudflared_token="${cloudflared_token%\"}"
      break
    fi
  done < "$ROOT_DIR/.env"
fi

bash "$ROOT_DIR/scripts/macos/start-trade-services.sh"

osascript - "$ROOT_DIR" "$cloudflared_token" <<'APPLESCRIPT'
on createServiceTab(mainWindow, workingDir, commandText)
  tell application "iTerm"
    tell mainWindow
      create tab with default profile
    end tell
    delay 2
    tell current session of mainWindow
      write text "cd " & quoted form of workingDir
      write text "source .venv/bin/activate"
      write text commandText
    end tell
    delay 2
  end tell
end createServiceTab

on run argv
  set projectDir to item 1 of argv
  set cloudflaredToken to item 2 of argv
  set backendDir to projectDir & "/backend-futures-py"

  tell application "iTerm"
    activate
    set mainWindow to (create window with default profile)
    delay 2
  end tell

  createServiceTab(mainWindow, backendDir, "python monitor_mxf.py")

  if cloudflaredToken is not "" then
    tell application "iTerm"
      tell mainWindow
        create tab with default profile
      end tell
      delay 2
      tell current session of mainWindow
        write text "cloudflared tunnel run --token " & quoted form of cloudflaredToken
      end tell
      delay 2
    end tell
  end if

  createServiceTab(mainWindow, backendDir, "python webhook_server.py")
  createServiceTab(mainWindow, backendDir, "python monitor_render_ping.py")
end run
APPLESCRIPT

if [[ -z "$cloudflared_token" ]]; then
  echo "Warning: CLOUDFLARED_TOKEN is empty; the Cloudflare tab was skipped." >&2
fi
