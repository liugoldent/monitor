# 永豐 2：JSON 部位、04:59 清倉、05:05 重設

版本：`json-positions-v5`。

## 每日流程

- 12 個子策略的唯一追蹤部位來源是 `runtime/live_state.json` 的 `positions`。每個值只能是 -1、0、1。CSV 只提供新訊號及歷史查核，不再覆蓋 JSON 部位。
- 台北時間 08:45～13:45、15:00～隔天 05:00 為同一交易日；午休、15:00、午夜與程式重啟都不歸零。
- 04:59 查永豐 2 TMF 庫存並送一次清倉委託，保留 JSON 部位至確認空手。04:59 起停止新訊號交易。
- 04:59 清倉失敗或結果不明，Discord 明確通知人工核對及清倉，不自動重送。
- 05:05 查詢庫存及未結委託，確認 TMF 空手即將 12 個策略部位歸零。未確認則通知人工處理，開盤前最多每分鐘重查一次。到日曆規定的重新開盤時間（一般08:45），即使仍未確認空手，也將策略 JSON 歸零並處理新訊號；保留 `manual_flat_required`，不宣稱券商已空手，不因清倉失敗鎖定新單，也不再重設已開始的新時段部位。
- 停機錯過 05:05，啟動後補做檢查。成功重設記錄 `last_reset_cycle`，同一周期不重設第二次。週末及休市依 config/calendar.json 處理。
- 08:45 後，收到新訊號，計算 `(新部位 - positions[策略代碼]) × EF_HEDGE_SOURCE_UNIT`。預設每單位 1 口 TMF。
- 新訊號送單後的 JSON 合計淨部位限制為 -5～+5 口 TMF（含邊界，計入口數倍率）。超過上限只發 Discord、不送單，保留該策略原部位並消耗訊號，不補單；未超過且差額非零則照常通知及送單。04:59 清倉不受上限限制，仍查實際庫存、通知及送清倉單。上限依 JSON 追蹤部位計算，不是券商實際庫存保證。
- 啟動前、停機期間與等待重設期間已收到的訊號不補單。

| JSON 原部位 | 新部位 | 委託（每單位1口） |
|---|---|---|
| 0 | 0 | 不送單 |
| 0 | +1 | 買1口 |
| 0 | -1 | 賣1口 |
| +1 | 0 | 賣1口 |
| -1 | 0 | 買1口 |
| +1 | -1 | 賣2口 |
| -1 | +1 | 買2口 |

## 重啟與送單狀態

送單前將 positions、新訊號進度、attempt 一起原子保存。一般委託只送一次，不查成交、不重試。JSON 是策略追蹤部位，不是成交證明；拒單、未成交、手動交易仍可能造成券商實際庫存差異。

若重啟發現 `attempt.status` 還是 `attempted`，代表上次在送單途中中斷，會設定 `blocked_reason`。若中斷的是04:59清倉，新時段重設會將它轉為人工清倉待辦並解除該次暫停；一般訊號委託中斷的保護仍保留。人工處理的券商殘留部位與新時段策略 JSON 分開，JSON 歸零不代表券商空手。

暫停新單期間仍讀取新 EF 訊號，每筆通知「收到EF訊號・暫停下單」及原因，並記錄 `signal_blocked`。獨立的 `blocked_signal_cursor` 防止重複通知與解除暫停後補單，不修改 JSON 部位或中斷委託。正常收到訊號但 JSON 差額為0時，也通知「無需下單」。啟動前的舊訊號仍不補通知。

首次升級 v4 時，從既有 `day_signal_positions` 快照遷移一次到 `positions`，保留當日部位，不回放 CSV。遷移後刪除 `day_signal_positions`；每次存檔也移除 `source.positions`、`source.net_position`，避免保存重複且可能過期的部位快照。`source.last_signal` 與該筆訊號明細仍保留作為處理進度；手動修改部位只改最外層 `positions`。

## 手動修改

1. 停止 `ef-morning-weekend-hedge-strategy` 服務。
2. 備份 `runtime/live_state.json`，核對券商實際部位及委託紀錄。
3. 只調整 `positions` 中對應策略的 -1 / 0 / 1，必須保留全部12個策略。
4. 啟動服務，JSON 會直接沿用；修改檔案本身不會觸發補單。

如果在處理中斷委託，核對完成後另外將該 `attempt.status` 改為 `operator_reconciled`，並移除 `blocked_reason`；保留原委託 key、id 及其他歷史欄位。正常日常修改不需調整這些欄位。請勿在服務運行中改檔，程式不會即時重新載入，並可能覆寫人工修改。

## 帳戶、部署及紀錄

只使用 API_KEY2／SECRET_KEY2，通知使用 DISCORD_EF_hedge_WEBHOOK_URL。PERSON_ID2／CA_PATH2 可覆寫憑證，EF_HEDGE_ACCOUNT_ID 可核對帳號。合約 TMFR1、市價 MKT、IOC、Auto。EF_HEDGE_SIGNAL_CSV／EF_HEDGE_CALENDAR_PATH 可覆寫來源與日曆。

重新建置並只重啟此服務：

```sh
docker compose up -d --build --no-deps --force-recreate ef-morning-weekend-hedge-strategy
```

啟動通知須顯示 `json-positions-v5`。`python monitor_and_trade.py` 與 `--once` 都是實單入口，不可拿來當測試。

- runtime/live_state.json：positions、訊號進度、單次委託與重設狀態，原子保存。
- records/live_order_attempts.csv：委託嘗試及回應。
- records/live_events.csv：重設與異常事件。
- records/notifications.jsonl：通知紀錄。
- runtime/monitor.lock：避免同目錄多個服務並行。

測試採模擬券商，不連線實單：

```sh
python -m unittest discover -s tests -v
```

backtest.py 為理想成交回放，不模擬本版 JSON 手動修改、拒單或重設阻擋。
