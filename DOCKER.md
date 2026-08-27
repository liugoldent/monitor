# Docker 啟動說明

這份文件說明共用 Docker image 與 Compose 設定。平台入口另見：

- `docs/platform/macos.md`
- `docs/platform/windows.md`

原本的 `run-services.sh` 會用 macOS 的 `osascript` 開 iTerm 分頁；Docker 內不能跑 iTerm。
現在 `run-services.sh` 在容器內會自動改跑：

```text
scripts/run-services-docker.sh
```

## 啟動

### 單純訊號接收模式（預設）

在 repo 根目錄執行：

```bash
docker compose up --build
```

背景執行：

```bash
docker compose up -d --build
```

Windows 也可直接執行：

```text
run-windows-services.cmd
```

它只啟動純記錄服務，並開啟 `Telegram H-EF Relay`、`MXF Market Monitor`、
`Webhook Server`、`Cloudflare Tunnel` 四個 log 頁籤；舊策略會先被停止。

看 log：

```bash
docker compose logs -f telegram-signal-relay
```

停止：

```bash
docker compose down
```

預設啟動 `telegram-signal-relay`、`webhook-server` 與 `monitor-mxf`。
它們不計算交易策略、不連券商、不下單，只維持四份核心資料：

```text
Telethon H  訊號 -> DISCORD_H_TRADE_WEBHOOK_URL
Telethon EF 訊號 -> DISCORD_SIX_STRATEGY_WEBHOOK_URL
EF 訊號 -> backend-futures-py/tv_doc/six_strategy_signal_events.csv
H 進出場 -> backend-futures-py/tv_doc/h_trade.csv
H 原始部位事件 -> backend-futures-py/h3-ef-012-strategy/records/h3_position_events.csv
EF 原始部位事件 -> backend-futures-py/h3-ef-012-strategy/records/ef_position_events.csv
TradingView 1 分 K -> backend-futures-py/tv_doc/webhook_data_1min.csv
MXF 籌碼 -> backend-futures-py/tv_doc/mxf_value.csv
Telegram 稽核紀錄 -> backend-futures-py/telegram-relay-records/telegram_signal_events.jsonl
```

`cloudflared` 仍是手動啟動，避免 Docker 啟動時未經確認就對外開放服務。
需要既有 tunnel 時執行：

```powershell
docker compose --profile tunnel up -d cloudflared
```

原本的整合容器已移到 `legacy` profile，除非明確執行以下命令才會啟動：

```bash
docker compose --profile legacy up monitor
```

### 已停用的策略服務

策略已放入獨立 `strategies` profile，不會隨一般 Docker 啟動。若日後要明確恢復：

```powershell
docker compose --profile strategies up -d --build
```

一般 `docker compose up` 不會啟動這個 profile。

## 對外 port

```text
8080  webhook_server.py
5050  mongo_market_api.py
5173  frontend-vue vite dev server
```

## Cloudflare tunnel

不要把 token 寫進 git。

如果要啟動 cloudflared，複製範本：

```bash
cp docker.env.example .env
```

然後把 `.env` 內的 `CLOUDFLARED_TOKEN=` 補上真實 token。
`docker compose` 會自動讀 repo 根目錄的 `.env`。

如果 `CLOUDFLARED_TOKEN` 是空的，容器會跳過 cloudflared，但其他服務照常啟動。

## 重要掛載檔案

Docker image 不會打包 `.env` 和 `.pfx`，避免憑證被 bake 進 image。
`docker-compose.yml` 會從本機掛載這些檔案：

```text
backend-futures-py/.env
backend-futures-py/Sinopac.pfx
backend-futures-py/session_monitor_six_strategy.session
backend-heyu-node/.env
frontend-vue/.env
```

Windows 那台如果缺其中任何一個檔案，請先補齊。

H3+EF 服務另會使用下列本機 runtime 目錄；內容已由該策略的 `.gitignore`
排除：

```text
backend-futures-py/h3-ef-012-strategy/runtime/
```

## 只跑部分服務

可以用 `SERVICE_FILTER` 只跑指定服務，方便測試。

```bash
SERVICE_FILTER=webhook-server,mongo-market-api,frontend-vue docker compose up --build
```

服務名稱：

```text
six-strategy
heyu-node
monitor-mxf
monitor-stock-futures
monitor-pocket-etf
webhook-server
frontend-vue
monitor-render-ping
mongo-market-api
cloudflared
```
