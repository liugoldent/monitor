# Windows 啟動說明

## 前置檔案

Windows 主機必須自行準備以下不進 Git 的檔案：

```text
.env                              # CLOUDFLARED_TOKEN；缺少時啟動器會要求輸入
backend-futures-py/.env           # API 與 Telegram 設定
backend-futures-py/Sinopac.pfx    # 永豐憑證
```

## 啟動

在 repo 根目錄雙擊：

```text
run-windows-services.cmd
```

它會依序：

1. 執行 `git pull --ff-only` 拉取目前分支更新；失敗時停止啟動流程，不重啟服務。
2. 檢查必要檔案與 Docker；群益H3、凱基H3暫停自動啟動，不檢查其 SDK 或帳戶設定。
3. 必要時啟動 Docker Desktop並等待引擎就緒。
4. 停止全部 EF 策略與 legacy 整合容器，必要時重新 build，啟動下列 8 個服務。
5. 在 Windows Terminal 開各服務的 log 分頁；沒有 Windows Terminal 時改開 PowerShell。

2026-10-18 起，一鍵啟動不再啟動群益H3或凱基H3，也不開啟兩者日誌分頁。
各自的策略、SDK、設定及交易紀錄保留；手動啟動工具仍可使用。
此項修改僅停用自動啟動，不會自動終止已運行的原生程序。

已確認的整筆拒單在零成交、庫存一致及回報可關聯時會結案並繼續監控，同一訊號不重送。未知結果、部分成交、斷線或庫存不明仍停止核對，日誌會留下原因。關閉日誌分頁不會停止策略。

三個永豐策略進入監控迴圈後立即輸出中文心跳，此後每60秒輸出一次，包含台北時間、帳戶標籤、正式／模擬模式、帳本口數及啟動後最近讀取訊號的時間。心跳不查詢券商，不代表即時庫存核對；新訊號讀取、策略啟動及成交通知也同步輸出至日誌。異常時顯示既有的重試／等待核對訊息。


`ef-hysteresis-again-strategy` 使用 `API_KEY` / `SECRET_KEY` 的 Shioaji 模擬帳戶（`simulation=True`）；
`ef-morning-weekend-hedge-strategy` 使用第二組 `API_KEY2` / `SECRET_KEY2`，
也使用 Shioaji 模擬帳戶（`simulation=True`），不以 `EF_PURE_FLAT_ENABLE_ORDERS` 切換。
兩套策略移至 `ef-simulation` profile，現在都不自動啟動；程式、設定與紀錄保留。

Windows 一鍵啟動會額外啟用 `h3-protection` 與 `h3-shane` profiles，同時啟動三個永豐策略。Shane 使用獨立的 `backend-futures-py/h3-shane-use/.env`，須設定 `H3_SHANE_ENABLE_ORDERS=true`；目前為模擬交易，憑證路徑可設為 `CA_PATH=Sinopac_shane.pfx`：

| 服務 | 用途 |
| --- | --- |
| `telegram-signal-relay` | 繼續記錄 H3／EF 訊號並轉發 Discord |
| `h3-signal-router` | 康和本機優先、TG備援，去重後供三個永豐策略讀取（[說明](../h3-dual-source-signals.md)） |
| `monitor-mxf` | 籌碼、游擊資料監控與記錄 |
| `webhook-server` | 接收並保存 TradingView 行情 |
| `cloudflared` | 維持 TradingView webhook 對外連線 |
| `h3-flatten-reentry-strategy` | API_KEY／SECRET_KEY H3 游擊加碼（正式帳戶） |
| `h3-profit-protection-strategy` | API_KEY2 H3 純跟單，依 H_UNIT_KEY2 設定口數（目前正式帳戶），無浮盈回吐平倉 |
| `h3-shane-use` | Shane H3 跟單（模擬帳戶），每筆新的方向訊號先平再進 SHANE_UNIT 口 |

第一次取得新版啟動器，請先在 Windows 專案目錄執行一次 `git pull --ff-only`，再雙擊啟動器。之後可直接執行啟動器完成拉取與建置。拉取失敗時保留現有服務，不會自動 reset、stash 或覆蓋本機修改。

只啟動本機版本，不拉取更新：

```powershell
.\run-windows-services.ps1 -NoPull
```

不重新 build image（程式更新後請勿使用，舊映像不會套用新程式）：

```powershell
.\run-windows-services.ps1 -NoBuild
```

## 停止

```text
stop-windows-services.cmd
```

停止總腳本會一併停止 API_KEY、API_KEY2、Shane 三個永豐策略與其餘 Docker 服務，也停止群益H3及凱基H3；不自動平倉或撤單。即使原生服務停止失敗，仍會嘗試停止 Docker 服務。關閉log視窗不會停止交易服務。

## 單獨初始化 Telegram

```text
initialize-telegram-relay-session.cmd
```

輸入電話時可使用 `09xxxxxxxx` 或 `+8869xxxxxxxx`。

## 永豐 2 H3 跟單

2026-10-29 起，雙擊 `run-windows-services.cmd` 同時啟動 API_KEY 正式游擊策略與 API_KEY2 H3 純跟單，並開啟各自日誌分頁。API_KEY2 於 2026-10-29 依使用者修改切為 `BROKER_SIMULATION=False`（真實下單）；實盤與模擬帳本分開；`backend-futures-py/.env` 須設定 `H3_PROTECTION_ENABLE_ORDERS=true`（本機已設定）。

API_KEY2 新 H3 訊號依 `H_UNIT_KEY2` 設定口數進場、不加碼；H3 平倉就全平，反向先平再進。已移除浮盈500點／回吐30%自動平倉，尚未加入日K均線條件。重啟不補舊訊號、不接管帳本外既有部位。舊 EF 策略仍不啟動。
