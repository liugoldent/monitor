# 永豐 2：新訊號單次下單、04:59 清倉

版本：`one-shot-signals-v3`。每次啟動只接新 EF 訊號；每筆最多送一次委託，不回查成交部位、不自動重試。

## 執行規則

- 啟動／重啟記下訊號 CSV 起點，不登入券商、不補舊部位、不處理停機期間訊號。
- 新訊號依時間及 CSV 列順序逐筆處理，不合併多筆。一般進出場直接以該筆差額送單，不查庫存、不對齊舊目標。
- 送單前保存已處理標記；每筆最多呼叫一次 `place_order`。收到 API 委託回傳就結束，不等待成交、不輪詢委託狀態、不回查成交後庫存。
- 拒單、逾時、登入失敗或其他例外都記錄為 `failed_no_retry`；該筆不重送、不阻擋下一筆訊號。`--retry-failed` 已移除。
- 每個有效夜盤 04:59 查詢第二帳戶 TMF 庫存，送一次反向差額清倉委託。當次失敗或結果不明亦不重送；保留每日標記，避免 04:59 期間重啟又送一次。
- 04:59 至下一個交易日 08:45 不建立新倉，08:45 不恢复舊部位，只接新訊號。13:45～15:00 休市訊號不補單。
- 04:59:40 後不送夜盤委託。持續執行期間若錯過清倉時段，於下一個可交易時段嘗試一次；普通重啟不補送舊清倉動作。

## 六種轉換

每策略預設 1 口：

| 訊號 | 委託 |
|---|---|
| -1 → 0 | 買 1 |
| 1 → 0 | 賣 1 |
| 0 → 1 | 買 1 |
| 0 → -1 | 賣 1 |
| 1 → -1 | 賣 2 |
| -1 → 1 | 買 2 |

每日清倉流程後，子策略從 0 開始記錄：收到舊部位的平倉訊號維持空手，收到反轉則建立新方向 1 口。清倉委託失敗亦不追補，須以券商實際紀錄確認持倉。

## 帳戶與送單

只使用 `API_KEY2`／`SECRET_KEY2` 與專用 `DISCORD_EF_hedge_WEBHOOK_URL`。合約使用 TMFR1，市價 MKT、IOC、Auto。第一帳號的策略與共用成交驗證流程不變；本策略只共用登入輔助、合約與委託格式，不呼叫共用的成交驗證執行器。

清倉前檢查其他月份、多空雙邊庫存與未結委託；如有異常，記錄本次失敗，不盲目以淨額清倉。一般訊號直接送其差額；不使用 `EF_HEDGE_MAX_CONTRACTS` 做總庫存對帳。

通知「已送出」僅表示取得委託回傳，不宣稱已成交。沒有回傳或即時拒單會通知失敗／結果不明，不重送。

## 設定與啟動

沿用上層 `.env`：

```dotenv
API_KEY2=第二組金鑰
SECRET_KEY2=第二組密鑰
PERSON_ID=憑證身分證字號
CA_PATH=/absolute/path/Sinopac.pfx
DISCORD_EF_hedge_WEBHOOK_URL=專用通知網址
EF_HEDGE_SOURCE_UNIT=1
```

`PERSON_ID2`／`CA_PATH2` 可覆寫憑證；`EF_HEDGE_ACCOUNT_ID` 選填核對帳號；`EF_HEDGE_SIGNAL_CSV`／`EF_HEDGE_CALENDAR_PATH` 可覆寫訊號及日曆。日曆需涵蓋日期；`closed_dates`、`no_night_dates` 設定休市日。

Windows 執行根目錄 `run-windows-services.cmd`：拉取更新、build，並以 `--force-recreate` 重建永豐 2。更新後不要使用 `-NoBuild`。Discord 啟動通知應顯示 `one-shot-signals-v3`；仍顯示「10 秒後自動重新對帳」即為舊版。

`python monitor_and_trade.py` 與 `--once` 均為第二帳號實單入口；啟動時不補單，但遇新訊號或清倉時間可能送單。

## 紀錄與驗證

- `runtime/live_state.json`：訊號進度與單次委託標記；使用原子寫入，啟動沿用檔案但不追補舊訊號。
- `records/live_order_attempts.csv`：`submission_attempt`、`submitted`、`no_order_needed`、`failed_no_retry`；不將送出誤記為成交。
- `records/live_events.csv` 與 `records/notifications.jsonl`：事件與非同步通知紀錄。
- `runtime/monitor.lock`：防止同目錄重複啟動。舊避險格式狀態須先處理，不與純 EF 混用。

```bash
python -m unittest discover -s tests -v
```

測試使用模擬券商，不連線實單。`backtest.py` 為理想成交回放，不模擬本版拒單或不重試造成的庫存差異。
