# 平台分支收斂紀錄

## 目標

讓同一份程式碼可在 macOS 與 Windows 執行，最終不再以 `master-win` 維護另一份
應用程式。重構期間 `master` 與 `master-win` 都保留，直到兩個平台完成實機驗證。

## 第一階段：平台入口分層

本階段只整理啟動與部署邊界，不修改交易策略邏輯。

### macOS

- 真正實作移至 `scripts/macos/`。
- 根目錄 `run-services.sh`、`run-trade-services.sh` 保留為相容入口。
- repo 路徑改為動態計算。
- Cloudflare token 改讀環境變數或 `.env`，不再寫在腳本內。

### Windows

- 真正實作移至 `scripts/windows/`。
- 根目錄 `.cmd`、`.ps1` 保留為相容入口。
- 從 `master-win` 移植四個實際使用的服務，不攜帶歷史 CSV、狀態資料或不相關刪除。
- Windows Compose services 使用 `windows` profile，避免影響既有整合容器。

### 共用

- Telegram session 初始化程式放在 `scripts/shared/`。
- 平台文件放在 `docs/platform/`。

## 合併前驗證

- [x] Bash 語法檢查。
- [x] Compose 預設設定解析；預設仍只有 `monitor`。
- [x] Compose `windows` profile 設定解析。
- [x] Telegram helper Python 編譯。
- [x] webhook tests：3 tests passed。
- [x] H3+EF tests：38 tests passed（專案 `.venv`）。
- [ ] macOS 實際啟動、接收 webhook、停止。
- [ ] Windows PowerShell 語法解析。
- [ ] Windows Docker Desktop 實際啟動四個服務。
- [ ] Windows Telegram session 初始化。
- [ ] Windows Cloudflare tunnel 對外連線。

只有未完成項目全部通過後，才考慮合併進 `master`。
