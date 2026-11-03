# Shane H3 跟單

讀取共用 `telegram-relay-records/h3_signal_events.jsonl`。每筆新的 H3 多單或空單訊號，不論目前方向，均先確認平掉原有實際口數，再依訊號方向進 `.env` 中的 `SHANE_UNIT` 口。明確空手訊號只平倉。同一訊號 ID 不重複下單；重啟從訊號檔尾開始，不補歷史訊號。券商庫存必須與本策略專用帳本一致，否則停止送單等待核對。未知、部分成交或未確認委託保留 guard，不自動重送。

目前使用永豐 `simulation=True`，帳號金鑰、個人身分、憑證路徑、預期期貨帳號與 `SHANE_UNIT` 全部從本目錄 `.env` 載入，不讀取上層 `.env`。`SHANE_UNIT` 必須是正整數；修改後重啟服務才會套用，既有持倉仍按帳本實際口數全平，下次進場使用新 `SHANE_UNIT`。先複製 `.env.example` 為 `.env` 並填入資料；憑證可放本目錄或使用絕對路徑。`H3_SHANE_ENABLE_ORDERS=true` 才允許下單。

Compose 服務使用 `h3-shane` profile，預設啟動和 Windows 一鍵啟動都不會啟用。設定好後先執行：

```bash
docker compose --profile h3-shane run --rm --no-deps h3-shane-use python /app/backend-futures-py/h3-shane-use/monitor_and_trade.py --check-config
docker compose --profile h3-shane up -d --no-build h3-shane-use
```

交易帳本與紀錄分別存於本目錄 `runtime/`、`records/`。結算日沿用現有 H3 的 13:25 自動換月流程，若無法核對合約或委託即等待處理。
