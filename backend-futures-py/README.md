## 啟動環境
```bash
source venv/bin/activate
```


## 設定環境變數
```bash
建立 `.env`，並填入：
API_ID=你的 api_id
API_HASH=你的 api_hash
```

Telegram relay 也會同步監控群益顧問下單機的 `Bridge_Lite_Signal.log`。在 Docker
環境中，將 `CONSULTANT_SIGNAL_LOG_DIR` 設為包含該檔案的主機資料夾，例如：

```text
CONSULTANT_SIGNAL_LOG_DIR=C:/Program Files (x86)/MR.AutoTrading/群益期貨顧問策略下單機/Log
CONSULTANT_SIGNAL_LOG_PATH=/external/consultant-log/Bridge_Lite_Signal.log
CONSULTANT_SIGNAL_LOG_ENCODING=cp950
```

服務會從目前檔案尾端開始監控，並把讀取位置保存到
`telegram-relay-records/consultant_signal_log_checkpoint.json`。Telegram 與本機日誌
同一策略已經到達目標倉位後，再收到相同的倉位轉換會視為重送；策略先離開目標倉位，
之後再次走相同轉換才會視為新訊號。兩個來源的觀測仍會保留在
`telegram_signal_events.jsonl`。

## 開啟瀏覽器
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
--remote-debugging-port=9222 \
--remote-allow-origins='*' \
--user-data-dir="$HOME/chrome-debug"


# 手動補跑技術資料
cd /Users/kt/Desktop/self/monitor/backend-futures-py
.venv/bin/python fetch_stock_tech.py --min-count 3 --report-date YYYY-MM-DD --output-date YYYY-MM-DD --sleep 0.2

# 手動規格化某天報告
cd /Users/kt/Desktop/self/monitor/frontend-vue
PATH=/Users/kt/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH pnpm institutional:normalize 2026-05-08

# 每日完整產出順序
cd /Users/kt/Desktop/self/monitor/backend-futures-py
.venv/bin/python fetch_stock_tech.py --min-count 3 --report-date YYYY-MM-DD --output-date YYYY-MM-DD --sleep 0.2

cd /Users/kt/Desktop/self/monitor/frontend-vue
PATH=/Users/kt/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH pnpm institutional:normalize YYYY-MM-DD

# 自動化單一入口
cd /Users/kt/Desktop/self/monitor
bash scripts/build-institutional-report.sh YYYY-MM-DD

# 使用 $signal-replay-backtest 回測目前策略
