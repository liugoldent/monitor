# Docker 啟動說明

Windows 交易機的日常啟動、停止與再啟動流程，請先看 [`DOCKER-WINDOWS-SOP.md`](DOCKER-WINDOWS-SOP.md)。

這份 Docker 設定是給 Windows 或其他非 macOS 環境使用。

原本的 `run-services.sh` 會用 macOS 的 `osascript` 開 iTerm 分頁；Docker 內不能跑 iTerm。
現在 `run-services.sh` 在容器內會自動改跑：

```text
scripts/run-services-docker.sh
```

## 啟動

### Windows 交易機：單一 Telethon 長駐模式

Windows 專用內容位於 `master-win` 分支。先切換分支：

```powershell
git switch master-win
```

然後雙擊：

```text
run-windows-services.cmd
```

第一次執行會要求輸入新的 Cloudflare tunnel token，並將它存到 Git 忽略的根目錄 `.env`。啟動器會自動啟動 Docker Desktop、建立或更新 image，並啟動以下程序：

```text
monitor-mxf     monitor_mxf.py
webhook-server  webhook_server.py
h3-ef-012-strategy  同時監聽 H3 與十二策略 E/F
cloudflared     Cloudflare tunnel
```

若已安裝 Windows Terminal，畫面會開啟一個 Windows Terminal 視窗，內含四個 log 分頁：

```text
1. MXF 市場監聽
2. Webhook Server (`webhook_server.py`)
3. H3 + EF 0/U/2U 策略（含六策略相容寫檔）
4. Cloudflare Tunnel
```

若找不到 `wt.exe`，啟動器會退回四個獨立 PowerShell 視窗。可從 Microsoft Store 安裝 Windows Terminal 後再次執行啟動器。

H3 + EF 0/U/2U 第一次啟動前，先雙擊 `initialize-h3-ef-012-session.cmd`，用相同方式建立它自己的 Telegram session。session 會保存在策略目錄的 `runtime/`，不會提交到 Git。
若直接執行 `run-windows-services.cmd`，啟動器也會在發現 session 不存在時自動進入這個初始化流程。

關閉 log 視窗不會停止背景服務。容器設為 `restart: unless-stopped`，程序異常或 Docker/Windows 重新啟動後會自動恢復。

若希望 Windows 登入後不必手動開 Docker，請在 Docker Desktop 設定中啟用 `Start Docker Desktop when you sign in`。

停止這組服務可雙擊：

```text
stop-windows-services.cmd
```

### Windows：舊六策略獨立監聽器（Legacy，日常請勿啟動）

先開啟 Docker Desktop，然後在 PowerShell 執行：

```powershell
.\run-six-strategy.ps1
```

也可以直接雙擊 `run-six-strategy.cmd`。這個入口會自動使用 PowerShell 的 `Bypass` 模式，不受本機 execution policy 影響。

背景執行：

```powershell
.\run-six-strategy.ps1 -Background
```

查看 log：

```powershell
docker compose logs -f six-strategy
```

停止：

```powershell
docker compose stop six-strategy
```

成功連上 Telegram 後，符合條件的新訊號會寫入：

```text
backend-futures-py/tv_doc/six_strategy_signal_events.csv
```

這個專用服務只保留給除錯或回退。日常模式請使用 `h3-ef-012-strategy`，否則 E/F 訊號會被重複處理。

### Windows：啟動三個核心服務

H3 + EF 策略第一次使用時，先雙擊：

```text
initialize-h3-ef-012-session.cmd
```

之後在 repo 根目錄執行：

```powershell
docker compose stop six-strategy
docker compose up -d --build monitor-mxf webhook-server h3-ef-012-strategy
```

查看三個服務狀態與 H3 + EF log：

```powershell
docker compose ps
docker compose logs -f h3-ef-012-strategy
```

停止這三個服務：

```powershell
docker compose stop monitor-mxf webhook-server h3-ef-012-strategy
```

### 啟動全部服務

在 repo 根目錄執行：

```bash
docker compose up --build
```

背景執行：

```bash
docker compose up -d --build
```

看 log：

```bash
docker compose logs -f monitor
```

停止：

```bash
docker compose down
```

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
backend-heyu-node/.env
frontend-vue/.env
shioaji_demo_shane/.env
shioaji_demo_shane/Sinopac.pfx
shioaji_demo_rosco/.env
shioaji_demo_rosco/Sinopac.pfx
shioaji_demo_ichih/.env
shioaji_demo_ichih/Sinopac.pfx
```

Windows 那台如果缺其中任何一個檔案，請先補齊。

## 只跑部分服務

可以用 `SERVICE_FILTER` 只跑指定服務，方便測試。

```bash
SERVICE_FILTER=webhook-server,mongo-market-api,frontend-vue docker compose up --build
```

服務名稱：

```text
six-strategy (legacy profile)
h3-ef-012-strategy
trade-shane
heyu-node
monitor-mxf
monitor-stock-futures
monitor-pocket-etf
webhook-server
frontend-vue
monitor-render-ping
mongo-market-api
google-clockin
cloudflared
```
