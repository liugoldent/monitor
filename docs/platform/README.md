# 跨平台執行架構

程式碼只維護一份；作業系統差異集中在 `scripts/macos/` 與
`scripts/windows/`。Repo 根目錄若保留啟動檔，只能作為舊操作方式的相容入口。

## 目前狀態

- macOS：實作位於 `scripts/macos/`，根目錄 shell script 是相容入口。
- Windows：實作位於 `scripts/windows/`，根目錄 `.cmd`／`.ps1` 是相容入口。
- Docker：Windows 獨立服務使用 `windows` Compose profile；既有 `monitor`
  服務未加 profile，因此原本啟動方式不變。

## 變更原則

1. 共用 Python、Node 與前端程式不可因 OS 複製成兩份。
2. 平台差異優先放在啟動腳本或環境變數。
3. 每次只遷移一組可獨立驗證的服務。
4. Mac 與 Windows 都驗證完成前，不合併至 `master`。
