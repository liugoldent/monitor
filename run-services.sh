#!/bin/bash

osascript <<'EOF2'
tell application "iTerm"
  activate

  set mainWindow to (create window with default profile)
  set bounds of mainWindow to {0, 0, 1200, 600}

  -- ===== Tab 1 : Node =====
  -- Heyo打卡
  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-heyu-node"
    write text "source ~/.nvm/nvm.sh"
    write text "nvm use v22"
    write text "node index.js"
  end tell
  delay 2

  -- ===== Tab 2 : Python =====
  -- 台指期程式下單
  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_and_trade.py"
  end tell
  delay 2

  -- ===== Tab 3 : Python MXF =====
  -- get MXF 資料
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_mxf.py"
  end tell
  delay 2

  -- ===== Tab 4 : Python Stock Futures =====
  -- get 股票期貨資料
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_stock_futures.py"
  end tell
  delay 2

  -- ===== Tab 9 : Pocket ETF =====
  -- 每天 21:00 自動排程（monitor_pocket_etf.py 內排程）
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_pocket_etf.py"
  end tell
  delay 2

  -- ===== Tab 10 : Cloudflared Tunnel =====
  -- Webhook tunnel
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cloudflared tunnel run --token eyJhIjoiZDM2YTZkNDkzNGEzMjI5ZjI1ZWQwZjQwOWE0OTU1MzEiLCJ0IjoiNzhiM2VlZjctNGNiMy00N2ZhLWEwOGUtMWMzZTU0MWQ2YjgyIiwicyI6Ik1qVTNaV0psTjJNdE0yRTFaQzAwTWpCakxUa3daR0l0TUdZeE1XVmtOVGc0TXpOayJ9"
  end tell
  delay 2

  -- ===== Tab 11 : Webhook Server =====
  -- 接收 webhook 並寫入 CSV
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python webhook_server.py"
  end tell
  delay 2

  -- ===== Tab 12 : HQT Keedem Schedule =====
  # -- 08:00-09:00 上班 / 18:00-20:00 下班 每分鐘輸出
  # tell mainWindow
  #   create tab with default profile
  # end tell

  # tell current session of mainWindow
  #   try
  #     set name to "HQT Keedem Schedule"
  #   end try
  #   write text "printf '\\033]0;HQT Keedem Schedule\\a'"
  #   write text "cd ~/Desktop/self/monitor/google-clockIn"
  #   write text "source .venv/bin/activate"
  #   write text "python hqt_keedem_schedule_output.py"
  # end tell
  # delay 2
  
  -- ===== Tab 13 : Frontend =====
  -- 啟動 Frontend
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/frontend-vue"
    write text "source ~/.nvm/nvm.sh"
    write text "nvm use v22"
    write text "pnpm dev"
  end tell
  delay 2

  -- ===== Tab 14 : Render Ping =====
  -- 每 15 分鐘 GET 一次 monitor-9dtg.onrender.com
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_render_ping.py"
  end tell
  delay 2

  -- ===== Tab 15 : Python =====
  -- shane 台指期程式下單
  tell mainWindow
    create tab with default profile
  end tell
  delay 2

  tell current session of mainWindow
    write text "/Users/kt/Desktop/self/monitor/shioaji-demo-shane"
    write text "source .venv/bin/activate"
    write text "python monitor_and_trade.py"
  end tell
  delay 2

end tell
EOF2
