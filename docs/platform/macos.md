# macOS 啟動說明

相容入口維持不變：

```bash
./run-services.sh
```

根目錄腳本會轉交給：

```text
scripts/macos/start-services.sh
scripts/macos/start-trade-services.sh
```

實作仍使用 AppleScript 控制 iTerm，因此只適用於 macOS。專案路徑會依 repo 實際
位置計算，不再假設一定放在 `~/Desktop/self/monitor`。

Cloudflare token 依序讀取：

1. shell 的 `CLOUDFLARED_TOKEN` 環境變數；
2. repo 根目錄 `.env` 內的 `CLOUDFLARED_TOKEN`。

兩處都沒有設定時，其他服務照常啟動，但略過 Cloudflare tunnel。
