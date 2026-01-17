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
