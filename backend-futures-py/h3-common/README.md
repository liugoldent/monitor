# H3 共用模組

`h3-follow-add-strategy` 與 `h3-profit-protection-strategy` 共用此目錄：

- `h_core.py`：訊號解析、持久化狀態、委託防重與錯誤類型。
- `recovery_broker.py`：永豐委託核對介面；交易紀錄目錄由呼叫策略明確傳入。
- `trade_records.py`：成交紀錄格式與 CSV 寫入。

策略啟動時將本目錄加入模組搜尋路徑。Docker 將整個目錄以唯讀方式掛載。
共用程式不存放帳戶憑證、交易帳本或程序鎖；各策略使用自己的紀錄與帳本。

`h3-flatten-reentry-strategy/runtime` 暫時保留原有程序鎖與歷史帳本檢查位置，
避免改動使用中服務的互斥規則。移動鎖需另作停機切換。
