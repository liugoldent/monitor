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

### 數據記錄＋API_KEY H3 游擊策略（預設）

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

它會開啟 `Telegram H-EF Relay`、`MXF Market Monitor`、`Webhook Server`、
`Cloudflare Tunnel`、`H3 Follow Add LIVE - API KEY`、`H3 Follow - API KEY2`，以及 `H3 Concord + Telegram Signals` 共 7 個日誌頁籤。
Windows 一鍵啟動會啟用 `h3-protection` profile，額外啟動 API_KEY2 的 H3 純跟單（2026-10-29 已切為真實下單）。
`h3-shane-use` 是獨立的 `h3-shane` profile，需填寫其目錄內 `.env` 後明確啟動；不隨一般 Compose 或 Windows 一鍵啟動。規則與操作見 [Shane H3 跟單](backend-futures-py/h3-shane-use/README.md)。
雙擊 `stop-windows-services.cmd` 會停止兩個永豐策略及共用 Docker 服務，不自動平倉或撤單。
群益、凱基原生 H3 及 EF 系列都不自動啟動。

看 log：

```bash
docker compose logs -f telegram-signal-relay
```

停止：

```bash
docker compose down
```

一般 Compose 預設啟動以下服務及 `h3-signal-router`（共6個）；Windows 一鍵啟動額外啟動 `h3-profit-protection-strategy`（API_KEY2 H3 純跟單），共7個。康和本機優先與 TG 備援詳見 [雙來源說明](docs/h3-dual-source-signals.md)。

| 服務 | 用途 |
| --- | --- |
| `telegram-signal-relay` | H3／EF 訊號及原始事件記錄、Discord 轉發 |
| `h3-signal-router` | 唯讀查詢康和本機 H3，與 TG 備援去重後供兩策略讀取 |
| `monitor-mxf` | MXF 籌碼與股威游擊監控、資料記錄 |
| `webhook-server` | 接收並保存 TradingView K 棒 |
| `cloudflared` | 維持 TradingView webhook 對外連線 |
| `h3-flatten-reentry-strategy` | API_KEY／SECRET_KEY 正式帳戶，H3 浮盈500點＋游擊穿越加碼 |

EF Again、純 EF 移至 `ef-simulation` profile；舊 Hysteresis 與 TOTAL Breakout 保留在
`shadow` profile，全部預設不啟動。EF 有券商連線的策略均為 `simulation=True`。
Windows 一鍵腳本會先停止先前的 EF 與 legacy 整合容器，再啟動上述服務。
停止策略程序不會自動平倉或撤單。

前三個服務繼續維持核心資料，EF 策略停用不影響 EF 原始訊號記錄：

```text
Telethon H  訊號 -> DISCORD_H_TRADE_WEBHOOK_URL
Telethon EF 訊號 -> DISCORD_SIX_STRATEGY_WEBHOOK_URL
EF 訊號 -> backend-futures-py/tv_doc/six_strategy_signal_events.csv
H 進出場 -> backend-futures-py/tv_doc/h_trade.csv
H 原始部位事件 -> backend-futures-py/telegram-relay-records/h_position_events.csv
EF 原始部位事件 -> backend-futures-py/telegram-relay-records/ef_position_events.csv
TradingView 1 分 K -> backend-futures-py/tv_doc/webhook_data_1min.csv
MXF 籌碼 -> backend-futures-py/tv_doc/mxf_value.csv
Telegram 稽核紀錄 -> backend-futures-py/telegram-relay-records/telegram_signal_events.jsonl
```

每次服務程序啟動時，H relay、EF relay 與交易策略各自會向專用
Discord webhook 發送一次啟動通知；relay 的通知結果也會寫入稽核 JSONL。

`cloudflared` 現在隨預設清單啟動，使用專案根目錄 `.env` 的 `CLOUDFLARED_TOKEN`。
單獨套用既有 tunnel 時執行：

```powershell
docker compose up -d cloudflared
```

原本的整合容器已移到 `legacy` profile，除非明確執行以下命令才會啟動：

```bash
docker compose --profile legacy up monitor
```

## 對外 port

```text
8080  webhook_server.py
5050  mongo_market_api.py（僅 legacy 整合容器）
5173  frontend-vue vite dev server（僅 legacy 整合容器）
```

## Cloudflare tunnel

不要把 token 寫進 git。

如果要啟動 cloudflared，複製範本：

```bash
cp docker.env.example .env
```

然後把 `.env` 內的 `CLOUDFLARED_TOKEN=` 補上真實 token。
`docker compose` 會自動讀 repo 根目錄的 `.env`。

Cloudflare 需要有效 token 才能建立 tunnel；Windows 一鍵腳本會在缺少 token 時要求輸入。

## 重要掛載檔案

Docker image 不會打包 `.env` 和 `.pfx`，避免憑證被 bake 進 image。
`docker-compose.yml` 會從本機掛載這些檔案：

```text
backend-futures-py/.env
backend-futures-py/Sinopac.pfx
backend-heyu-node/.env
frontend-vue/.env
```

Windows 那台如果缺其中任何一個檔案，請先補齊。

Telegram relay 會使用下列本機 runtime 目錄；session 內容由根目錄
`.gitignore` 排除：

```text
backend-futures-py/telegram-relay-runtime/
```

需要重新授權 Telegram 時，在 Windows 執行：

```text
initialize-telegram-relay-session.cmd
```

## 只跑部分服務

`SERVICE_FILTER` 僅供 legacy 整合容器內的啟動腳本選擇服務；一般 Compose 請直接指定服務名稱。

```bash
SERVICE_FILTER=webhook-server,mongo-market-api,frontend-vue docker compose up --build
```

服務名稱：

```text
telegram-relay
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
