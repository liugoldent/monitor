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
2. 檢查必要檔案與 Docker。
3. 必要時啟動 Docker Desktop並等待引擎就緒。
4. 重新 build 並啟動七個 Docker 服務，包含 `ef-strong-consensus-morning-flat-strategy`、`ef-morning-weekend-hedge-strategy` 與 `options-level-monitor`。
5. 在 Windows Terminal 開七個分頁顯示各服務 log；沒有 Windows Terminal 時改開 PowerShell。

`ef-morning-weekend-hedge-strategy` 使用第二組 `API_KEY2` / `SECRET_KEY2`，
監控入口固定使用實單，不以 `EF_PURE_FLAT_ENABLE_ORDERS` 切換。
兩套策略會一併啟動，也會由停止腳本一併停止。

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

關閉 log 視窗不會停止 Docker 服務，必須使用停止腳本。

## 單獨初始化 Telegram

```text
initialize-telegram-relay-session.cmd
```

輸入電話時可使用 `09xxxxxxxx` 或 `+8869xxxxxxxx`。

## 2026-09-14 永豐 2 補倉

本次 `config/catch_up.json` 限定 2026-09-14 08:45 至 2026-09-15 04:59 前，讓第二帳號納入今天開市後的全部 EF 子策略訊號，不受原本啟動時間限制。重建後首次對帳會查券商實際庫存，依 Windows 最新 CSV 的目標補差額；目標多 4、實際多 1 時買 3，已多 4 時不送單。啟動時若訊號已變更，跟隨最新目標，不固定追買 4 口。

04:59 補倉設定自動失效並清倉；下一個交易日 08:45 只接新訊號。不要刪除 runtime；重啟須保留部位與重試狀態。此設定不代表已成交，須以委託結果通知為準。
