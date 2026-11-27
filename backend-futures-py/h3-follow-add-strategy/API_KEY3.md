# 永豐 API_KEY3 條件跟單

| 策略腿 | 啟動條件 | 解除條件 |
|---|---|---|
| H3 最多 1 口 | H3 單筆已平倉虧損至少 1,000 點，或 H3 連輸 2 筆 | H3 回到原始單口累計損益前高 |
| H2 最多 1 口 | H2 連輸 4 筆 | H2 回到原始單口累計損益前高 |
| H2 最多 1 口 | H3 原始單口回撤至少 750 點 | H3 回到原始單口累計損益前高 |

達標後，下一筆對應策略的進場訊號才跟單。H3 跨策略啟動時，H2 的進場時間戳須較晚。回撤歸零確認當下解除相應條件。H2 的兩條條件同時有效時維持 1 口，兩條都解除才把目標歸零。H3 共用同一個解除條件，最多 1 口。

同向相加，反向相抵，帳號只送淨部位差額。沿用永豐微台實際月份的 MKT／IOC／Auto、同月撤單、庫存核對、委託保護及結算日換月。策略部位、虛擬曲線及已處理訊號隨成交一起入帳。未確認委託須核對原單與庫存後才能繼續。

## 歷史和啟動

- H2 從 TradingView 逐筆匯出回溯，讀取配對進出場及最後未平倉輪次。預設尋找 `runtime/api_key3/h2.csv`，其次使用者的 `Downloads/h2.csv`。可用 `--h2-history` 或 `API_KEY3_H2_HISTORY` 指定位置。
- H3 從既有 `../tv_doc/h_trade.csv` 第一筆回溯，用進出場價換算單口毛損益。接續階段，H2 讀取可信 Telegram 訊號，H3 讀取已驗證的雙訊號來源。虛擬成交取訊號當時已收到的 `MXF1!` 一分鐘 `Close`，K 棒時間距訊號最多 120 秒。此價格用於策略曲線，實際交易仍走永豐市價委託。
- 首次啟動沿用歷史尚未回前高的條件，等新訊號進場。重啟使用 `runtime/api_key3/api_key3.sqlite`。停機期間的訊號仍補入虛擬曲線，歷史確認的解除事件可退出帳本中的舊部位。進場須通過 30 秒時效。
- H2 匯出應涵蓋至即時訊號可接續的位置。若中間有交易缺漏，請用較新的 H2 匯出補齊歷史。
- 登入讀取 `../.env` 的 `API_KEY3`、`SECRET_KEY3`、`PERSON_ID3`。憑證依序使用 `CA_PATH3`、`CA_PATH` 或 `../Sinopac.pfx`。`API_KEY3_ACCOUNT_ID` 必須填第三帳號的永豐期貨帳號，以便核對券商回傳的帳戶。首次連線會把證券商及期貨帳號綁定專用帳本。通知使用同一份 `.env` 的 `DISCORD_INCREASE_INVESTMENT`。

## 本機檢查及執行

在具有專案依賴的 Python 環境中，從專案根目錄執行離線檢查，會印出歷史筆數、回撤、連輸和目前條件。

```powershell
python backend-futures-py/h3-follow-add-strategy/api_key3_monitor.py --check-config
```

Docker 使用 `runtime/api_key3/h2.csv` 作為第一次回溯的 H2 匯出檔。把匯出放到該目錄後，可在容器內離線檢查。

```powershell
docker compose run --rm --no-deps api-key3-follow python /app/backend-futures-py/h3-follow-add-strategy/api_key3_monitor.py --check-config
```

`docker compose up` 與 Windows 一鍵啟動會啟動 `api-key3-follow`。它有獨立的容器、永豐連線、帳本及記錄。Windows 一鍵啟動會先啟動原有服務，再啟動 API_KEY3，API_KEY3 啟動失敗只在自己的日誌顯示。核對帳號、拒單或庫存錯誤也在 API_KEY3 容器內處理。

補上設定後，可用 `docker compose restart api-key3-follow` 只重啟 API_KEY3，或用 `docker compose logs -f api-key3-follow` 查看日誌。
