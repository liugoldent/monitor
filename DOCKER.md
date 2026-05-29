# Docker 啟動說明

這份 Docker 設定是給 Windows 或其他非 macOS 環境使用。

原本的 `run-services.sh` 會用 macOS 的 `osascript` 開 iTerm 分頁；Docker 內不能跑 iTerm。
現在 `run-services.sh` 在容器內會自動改跑：

```text
scripts/run-services-docker.sh
```

## 啟動

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
trade-main
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
