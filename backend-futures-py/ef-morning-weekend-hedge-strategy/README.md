# 永豐 2：JSON 部位、01:00 清倉、05:05 重設

CFCTX15m（財神列車15號）屬於 E 投組，與其餘 12 組策略一同追蹤。舊有 12 組 JSON 升級時保留原部位，新增 CFCTX15m 的追蹤部位為 0，僅處理啟動後的新訊號。

版本：`json-positions-v5-clamp`。

2026-09-18 送單修正：沿用 `shioaji-demo` 的 `place_order(..., timeout=0)`，
TMFR1 / MKT / IOC / Auto 格式不變。API 返回後原子保存
`submitted` 或 `no_order_needed`，避免程序中斷讓已返回的委託停留在 `attempted`。
`submitted` 只代表送單呼叫返回，不保證券商受理或成交；不回查、不重送。
送單及 API 返回均有分段 log，主程式啟用 faulthandler。
若在 API 返回及持久化之前中斷，該筆不重送；不鎖定後續訊號。下一筆新 EF 訊號會重新查券商庫存並計算新差額。

2026-09-21 連線修正：服務啟動時登入一次 Shioaji，送單與 05:05 檢查共用同一個
程序長效連線；不再於每筆訊號後登出，避免 pysolace 原生資源反覆建立／清理造成程序崩潰。

## 每日流程

- 13 個子策略的唯一追蹤部位來源是 `runtime/live_state.json` 的 `positions`。每個值只能是 -1、0、1。CSV 只提供新訊號及歷史查核，不再覆蓋 JSON 部位。
- 台北時間 08:45～13:45、15:00～隔天 05:00 為同一交易日；午休、15:00、午夜與程式重啟都不歸零。
- 01:00 查永豐 2 TMF 庫存並送一次清倉委託，保留 JSON 部位至確認空手。01:00 起停止新訊號交易。
- 01:00 清倉失敗或結果不明，Discord 明確通知人工核對及清倉，不自動重送。
- 05:05 查詢庫存及未結委託，確認 TMF 空手即將 13 個策略部位歸零。未確認則通知人工處理，開盤前最多每分鐘重查一次。到日曆規定的重新開盤時間（一般08:45），即使仍未確認空手，也將策略 JSON 歸零並處理新訊號；保留 `manual_flat_required`，不宣稱券商已空手，不因清倉失敗鎖定新單，也不再重設已開始的新時段部位。
- 停機錯過 05:05，啟動後補做檢查。成功重設記錄 `last_reset_cycle`，同一周期不重設第二次。週末及休市依 config/calendar.json 處理。
- 收到新訊號後，先更新該策略的 JSON 部位，計算全部策略的最終目標口數，再查永豐 2 當下 TMF 實際庫存。本次委託固定為 `最終目標口數 - 券商目前庫存`。
- 13 策略 JSON 保留完整合計淨方向，券商目標採 Clamp：淨方向大於 0 時目標為 `+U`，小於 0 時目標為 `-U`，等於 0 時空手。每筆訊號都先更新 JSON，再查永豐 2 當下 TMF 實際庫存並送出與 Clamp 目標的差額；U 由 `EF_MORNING_WEEKEND_HEDGE_UNIT` 設定。即使原始淨方向超過 ±1，也不會因超限而跳過下單。01:00 清倉仍固定以 0 為目標。
- 啟動前、停機期間與等待重設期間已收到的訊號不補單。

| 券商目前庫存 | 策略最終口數 | 本次委託 |
|---|---|---|
| 0 | +1 | 買進1口 |
| +2 | +1 | 賣出1口 |
| -1 | +1 | 買進2口 |
| +1 | -1 | 賣出2口 |
| +1 | +1 | 無需下單 |

## 重啟與送單狀態

每筆新 EF 訊號都先更新並計算 JSON 淨方向，再將正數 Clamp 為 +1、負數 Clamp 為 -1、零維持 0，乘上 `EF_MORNING_WEEKEND_HEDGE_UNIT` 後查券商 TMF 庫存、計算差額並送出一筆 IOC 委託。不掃描、不等待前一筆委託狀態，也不因中斷記錄鎖定新訊號。

送單前將 positions、新訊號進度、目前庫存、最終口數與預計委託一起保存。一般委託只送一次，不查成交、不重試。如果程式在送單途中中斷，該筆標記為 `interrupted_no_retry`，後續新訊號仍照常以當下券商庫存重新計算。

首次升級 v4 時，從既有 `day_signal_positions` 快照遷移一次到 `positions`，保留當日部位，不回放 CSV。遷移後刪除 `day_signal_positions`；每次存檔也移除 `source.positions`、`source.net_position`，避免保存重複且可能過期的部位快照。`source.last_signal` 與該筆訊號明細仍保留作為處理進度；手動修改部位只改最外層 `positions`。

## 手動修改

1. 停止 `ef-morning-weekend-hedge-strategy` 服務。
2. 備份 `runtime/live_state.json`，核對券商實際部位及委託紀錄。
3. 只調整 `positions` 中對應策略的 -1 / 0 / 1，必須保留全部13個策略。
4. 啟動服務，JSON 會直接沿用；修改檔案本身不會觸發補單。

請勿在服務運行中改檔，程式不會即時重新載入，並可能覆寫人工修改。

## 帳戶、部署及紀錄

只使用 API_KEY2／SECRET_KEY2，通知使用 DISCORD_EF_CLAMP_WEBHOOK_URL。PERSON_ID2／CA_PATH2 可覆寫憑證，EF_HEDGE_ACCOUNT_ID 可核對帳號。`EF_MORNING_WEEKEND_HEDGE_UNIT` 是下單倍數，允許 1～20，預設 1；策略方向永遠是 -1／0／+1，最終口數才乘上 UNIT。合約 TMFR1、市價 MKT、IOC、Auto。EF_HEDGE_SIGNAL_CSV／EF_HEDGE_CALENDAR_PATH 可覆寫來源與日曆。

重新建置並只重啟此服務：

```sh
docker compose up -d --build --no-deps --force-recreate ef-morning-weekend-hedge-strategy
```

啟動通知須顯示 `json-positions-v5-clamp`。目前 `auto_trade.py` 的 `api.place_order(...)` 已明確註解停用，因此不會送出實單；恢復該行前必須重新審核。`python monitor_and_trade.py` 與 `--once` 都不可拿來當測試。

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

## 實單通知

正式入口固定使用 API_KEY2 永豐實單。Discord 通知策略開始、訊號委託及 01:00 清倉。每筆訊號通知固定顯示來源策略、訊號動作、券商目前庫存、本次預計下單及收到策略後的最終口數。委託已送出不表示已成交。
