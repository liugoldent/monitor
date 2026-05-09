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

## 開啟瀏覽器
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
--remote-debugging-port=9222 \
--remote-allow-origins='*' \
--user-data-dir="$HOME/chrome-debug"


# 手動補跑技術資料
cd /Users/kt/Desktop/self/monitor/backend-futures-py
.venv/bin/python fetch_stock_tech.py --sleep 0.2

# 手動規格化某天報告
cd /Users/kt/Desktop/self/monitor/frontend-vue
PATH=/Users/kt/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH node scripts/normalize-institutional-report.mjs 2026-05-08
