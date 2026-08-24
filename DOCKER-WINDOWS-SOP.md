# Windows Docker 服務操作 SOP

這份 SOP 用於 `master-win` 分支的三個核心服務：

```text
monitor-mxf           MXF 市場資料監聽
webhook-server        TradingView Webhook Server
h3-ef-012-strategy    單一 Telethon 監聽 H3 與十二策略 E/F
```

`cloudflared` 是選配的第四個服務，只有 Webhook 需要從外網進入時才需要啟動。舊 `six-strategy` 已改為 legacy profile，日常啟動不會再開第二條 Telethon 連線。

## 0. 執行位置

所有 PowerShell 指令都在專案根目錄執行：

```powershell
cd "C:\Users\USER\OneDrive\桌面\monitor"
```

執行前確認目前分支：

```powershell
git branch --show-current
```

應顯示：

```text
master-win
```

## 1. 第一次啟動前的初始化

### 1.1 開啟 Docker Desktop

先從 Windows 開始功能表開啟 Docker Desktop，等到顯示 Docker Engine 已經運行。

可用以下指令確認：

```powershell
docker info
```

指令若能正常顯示 Server 資訊，就可以繼續。

### 1.2 確認必要檔案

以下檔案必須存在：

```text
backend-futures-py/.env
backend-futures-py/Sinopac.pfx
```

### 1.3 初始化 H3 + EF Telegram session

這個步驟只需要在第一次啟動，或 session 失效時重新執行。

雙擊：

```text
initialize-h3-ef-012-session.cmd
```

依序輸入：

1. Telegram 電話，可輸入 `09xxxxxxxx` 或 `+8869xxxxxxxx`；腳本會自動轉換台灣國際格式。
2. Telegram 收到的登入碼。
3. 如有啟用兩步驗證，輸入 2FA 密碼。

完成後 session 會保存在：

```text
backend-futures-py/h3-ef-012-strategy/runtime/session_h3_ef_012.session
```

`runtime/` 已被 Git 忽略，不會把 Telegram session 提交到 repository。

## 2. 正常啟動

### 方式 A：啟動三個核心服務

```powershell
docker compose stop six-strategy
docker compose up -d --build monitor-mxf webhook-server h3-ef-012-strategy
```

`-d` 表示後台執行，關閉 PowerShell 視窗不會停止服務。

### 方式 B：核心服務加 Cloudflare Tunnel

雙擊：

```text
run-windows-services.cmd
```

如果 H3 + EF Telegram session 還不存在，這個啟動器會自動進入一次性登入流程，不需要先關閉視窗再另外執行初始化腳本。

這會自動：

1. 確認並必要時啟動 Docker Desktop。
2. Build 最新 image。
3. 停止舊 `six-strategy`，啟動三個核心服務與 `cloudflared`。
4. 開啟 Windows Terminal log 分頁。

若不想重新 build image：

```powershell
.\run-windows-services.ps1 -NoBuild
```

## 3. 啟動後檢查

查看服務狀態：

```powershell
docker compose ps
```

核心三服務的 `STATUS` 應為 `Up`，`monitor-six-strategy` 應維持停止。

查看所有核心服務的即時 log：

```powershell
docker compose logs -f monitor-mxf webhook-server h3-ef-012-strategy
```

只查看 H3 + EF 策略：

```powershell
docker compose logs -f h3-ef-012-strategy
```

按 `Ctrl+C` 只會離開 log 畫面，不會停止後台容器。

## 4. 停止

### 停止三個核心服務

```powershell
docker compose stop monitor-mxf webhook-server h3-ef-012-strategy
```

### 停止核心服務與 Cloudflare Tunnel

雙擊：

```text
stop-windows-services.cmd
```

或執行：

```powershell
docker compose stop six-strategy monitor-mxf webhook-server h3-ef-012-strategy cloudflared
```

`docker compose stop` 只停止容器，不會刪除容器、image、CSV、JSON 或 Telegram session。

## 5. 再啟動

服務曾經啟動並且程式碼沒有改變時，可直接執行：

```powershell
docker compose start monitor-mxf webhook-server h3-ef-012-strategy
```

如果有更新程式碼、Dockerfile、requirements 或 Compose 設定，使用：

```powershell
docker compose up -d --build monitor-mxf webhook-server h3-ef-012-strategy
```

若也要再啟動 Cloudflare Tunnel：

```powershell
docker compose start cloudflared
```

## 6. 單一服務重啟

例如只重啟 H3 + EF 策略：

```powershell
docker compose restart h3-ef-012-strategy
```

只重啟 Webhook Server：

```powershell
docker compose restart webhook-server
```

重啟後立即檢查 log：

```powershell
docker compose logs -f --tail 100 h3-ef-012-strategy
```

## 7. 常見狀況

### Docker Engine 未啟動

如果出現：

```text
failed to connect to the docker API
```

請先開啟 Docker Desktop，等待 Engine 完全啟動後再重試。

### H3 + EF 不斷重啟或 Telegram 要求登入

停止該服務後，重新雙擊：

```text
initialize-h3-ef-012-session.cmd
```

不要同時啟動兩個 `h3-ef-012-strategy` 實例，策略會使用 lock 檔防止重複監聽。

### Telethon 連線數量

日常模式只會啟動 `h3-ef-012-strategy` 這一個 Telethon process。它同時處理 H3 與 E/F 訊號，並寫入組合策略自己的 `records/`，以及原六策略相容的 `tv_doc/six_strategy_signal_events.csv` 與 `tv_doc/six_strategy_position_state.json`。不要另外啟動 legacy `six-strategy`，否則 E/F 會被兩個 process 重複監聽與寫入。

### 查看最近的錯誤

```powershell
docker compose logs --tail 200 h3-ef-012-strategy
docker compose logs --tail 200 monitor-mxf
docker compose logs --tail 200 webhook-server
```

### 確認容器是否結束

```powershell
docker compose ps -a
```

## 8. 快速指令摘要

```powershell
# 啟動／更新三個核心服務
docker compose stop six-strategy
docker compose up -d --build monitor-mxf webhook-server h3-ef-012-strategy

# 停止
docker compose stop monitor-mxf webhook-server h3-ef-012-strategy

# 再啟動（程式碼未改）
docker compose start monitor-mxf webhook-server h3-ef-012-strategy

# 狀態
docker compose ps

# H3 + EF log
docker compose logs -f h3-ef-012-strategy
```
