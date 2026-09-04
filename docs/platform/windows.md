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

1. 檢查必要檔案與 Docker。
2. 必要時啟動 Docker Desktop並等待引擎就緒。
3. 停止均值回歸與其他舊策略容器。
4. 啟動五個原有 Docker 服務與 `options-level-monitor`。
5. 在 Windows Terminal 開六個分頁顯示各服務 log；沒有 Windows Terminal 時改開 PowerShell。

不重新 build image：

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
initialize-h3-ef-012-session.cmd
```

輸入電話時可使用 `09xxxxxxxx` 或 `+8869xxxxxxxx`。
