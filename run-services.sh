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
  delay 1

  -- ===== Tab 2 : Python =====
  -- 台指期程式下單
  tell mainWindow
    create tab with default profile
  end tell
  delay 1

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_and_trade.py"
  end tell
  delay 1
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
  delay 1
  -- ===== Tab 4 : Python Stock Futures =====
  -- get 期貨即時資料
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_stock_futures.py"
  end tell
  delay 1
  -- ===== Tab 5 : Python Turnover =====
  -- get yahoo成交量資料
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_turnover.py"
  end tell
  delay 1
  -- ===== Tab 6 : Python Market API =====
  -- 啟動 Market API
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python mongo_market_api.py"
  end tell
  delay 1
  -- ===== Tab 7 : Chrome Debugger =====
  -- TradingView 需要的 Chrome remote debugging
  tell mainWindow to set newTab to (create tab with default profile)
  tell current session of mainWindow
    -- 修正重點：使用單引號包裹整個指令字串，避免 $符號被 AppleScript 誤判
    write text "/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --remote-allow-origins='*' --user-data-dir=\"$HOME/chrome-debug\" &"
  end tell
  delay 1
  -- ===== Tab 8 : TradingView Tech =====
  -- TradingView 技術指標資料抓取
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/backend-futures-py"
    write text "source .venv/bin/activate"
    write text "python monitor_tv_data.py"
  end tell
  delay 1
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
  delay 1
  -- ===== Tab 10 : HQT Keedem Schedule =====
  -- 08:00-10:00 上班 / 18:00-20:00 下班 每分鐘輸出
  tell mainWindow
    create tab with default profile
  end tell

  tell current session of mainWindow
    write text "cd ~/Desktop/self/monitor/google-clockIn"
    write text "source .venv/bin/activate"
    write text "python hqt_keedem_schedule_output.py"
  end tell
  delay 1
  -- ===== Tab 11 : Frontend =====
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
  delay 1

end tell
EOF2
