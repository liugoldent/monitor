# Shane H3 跟單

讀取共用 `telegram-relay-records/h3_signal_events.jsonl`。每筆新的 H3 多單或空單訊號，不論目前方向，均先查詢券商實際庫存，全平並確認空手後，再依訊號方向進 `.env` 中的 `SHANE_UNIT` 口。明確空手訊號只平倉。同一訊號 ID 不重複下單；重啟從訊號檔尾開始，只查倉等待新訊號，不立即平倉、不補歷史訊號。手動部分停利、全平或增減倉造成口數不同時，下一筆新訊號會同步實際剩餘口數全平。未知、部分成交或未確認委託保留 guard，不自動重送；查倉失敗、其他月份或同時多空庫存仍等待核對。

目前使用永豐 `simulation=True`，帳號金鑰、個人身分、憑證路徑、選填的預期期貨帳號與 `SHANE_UNIT` 全部從本目錄 `.env` 載入，不讀取上層 `.env`。`H3_SHANE_ACCOUNT_ID` 留空時使用登入後的預設期貨帳戶；有填才比對帳號，且登入後必須有可用的期貨帳戶。`SHANE_UNIT` 必須是正整數；修改後重啟服務才會套用，既有持倉仍按帳本實際口數全平，下次進場使用新 `SHANE_UNIT`。先複製 `.env.example` 為 `.env` 並填入資料；憑證可放本目錄或使用絕對路徑。`H3_SHANE_ENABLE_ORDERS=true` 才允許下單。

Compose 服務使用 `h3-shane` profile；Windows 一鍵啟動會連同 API_KEY、API_KEY2 策略一起啟動 Shane，並開啟獨立日誌分頁；一鍵停止也會一併停止 Shane，不自動平倉。一般 Compose 預設啟動仍不啟用此 profile。Docker 使用的 `.env` 憑證路徑可設為 `CA_PATH=Sinopac_shane.pfx`（相對本目錄），不要使用 Windows 絕對路徑。設定好後先執行：

```bash
docker compose --profile h3-shane run --rm --no-deps h3-shane-use python /app/backend-futures-py/h3-shane-use/monitor_and_trade.py --check-config
docker compose --profile h3-shane up -d --no-build h3-shane-use
```

交易帳本與紀錄分別存於本目錄 `runtime/`、`records/`。結算日沿用現有 H3 的 13:25 自動換月流程，若無法核對合約或委託即等待處理。
