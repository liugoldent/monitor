# 永豐 2：JSON 部位、多空對稱否決、01:00 清倉、05:05 重設

CFCTX15m（財神列車15號）屬於 E 投組，與其餘 12 組策略一同追蹤。舊有 12 組 JSON 升級時保留原部位，新增 CFCTX15m 的追蹤部位為 0，僅處理啟動後的新訊號。

版本：`json-positions-v7-symmetric-veto`。

多空對稱出場規則：當日已追蹤部位從 `0→-1` 的策略會否決多單；
從 `0→+1` 的策略會否決空單。只要對應策略仍持有該方向，
即使 13 策略合計方向相反，券商目標也為空手。否決者離開原部位後，
若合計仍指向原帳戶方向，收到該筆新訊號後可重新進場。
單純 `1→0` 或 `-1→0` 只按合計與仍有效的否決者決定目標，不單獨強制出場。
每日重設清除兩種否決者；昨天的部位不帶到今天。

2026-09-18 送單修正：沿用 `shioaji-demo` 的 `place_order(..., timeout=0)`，
TMFR1 / MKT / IOC / Auto 格式不變。API 返回後原子保存
`submitted` 或 `no_order_needed`，避免程序中斷讓已返回的委託停留在 `attempted`。
`submitted` 只代表送單呼叫返回，不保證券商受理或成交；不回查、不重送。
送單及 API 返回均有分段 log，主程式啟用 faulthandler。
若在 API 返回及持久化之前中斷，該筆不重送；不鎖定後續訊號。下一筆新 EF 訊號會重新查券商庫存並計算新差額。

2026-09-21 連線修正：服務啟動時登入一次 Shioaji，送單與 05:05 檢查共用同一個
程序長效連線；不再於每筆訊號後登出，避免 pysolace 原生資源反覆建立／清理造成程序崩潰。

永豐 SDK 回報斷線時暫停處理訊號，先等待自動重連。連續 120 秒未恢復則退出，
由 Docker Compose 的 `restart: unless-stopped` 重新啟動。恢復後略過斷線期間
尚未處理的舊訊號，不補送委託；下一筆新訊號會重新查券商庫存。
等待時間可由 `.env` 的 `EF_HEDGE_RECONNECT_TIMEOUT_SECONDS` 調整。

## 每日流程

- 13 個子策略的唯一追蹤部位來源是 `runtime/live_state.json` 的 `positions`。每個值只能是 -1、0、1。CSV 只提供新訊號及歷史查核，不再覆蓋 JSON 部位。`long_veto_strategies` 記錄由 `0→-1` 建立且仍持空的策略；`short_veto_strategies` 記錄由 `0→+1` 建立且仍持多的策略。
- 台北時間 08:45～13:45、15:00～隔天 05:00 為同一交易日；午休、15:00、午夜與程式重啟都不歸零。
- 01:00 查永豐 2 TMF 庫存並送一次清倉委託，保留 JSON 部位至確認空手。01:00 起停止新訊號交易。
- 01:00 清倉失敗或結果不明，Discord 明確通知人工核對及清倉，不自動重送。
- 05:05 查詢庫存及未結委託，確認 TMF 空手即將 13 個策略部位歸零。未確認則通知人工處理，開盤前最多每分鐘重查一次。到日曆規定的重新開盤時間（一般08:45），即使仍未確認空手，也將策略 JSON 歸零並處理新訊號；保留 `manual_flat_required`，不宣稱券商已空手，不因清倉失敗鎖定新單，也不再重設已開始的新時段部位。
- 停機錯過 05:05，啟動後補做檢查。成功重設記錄 `last_reset_cycle`，同一周期不重設第二次。週末及休市依 config/calendar.json 處理。
- 收到新訊號後，先更新該策略的 JSON 部位，計算全部策略的最終目標口數，再查永豐 2 當下 TMF 實際庫存。本次委託固定為 `最終目標口數 - 券商目前庫存`。
- 13 策略 JSON 保留完整合計淨方向。淨方向大於 0 且沒有空方否決者時目標為 `+U`，有空方否決者時為 0；淨方向小於 0 且沒有多方否決者時目標為 `-U`，有多方否決者時為 0；等於 0 時空手。每筆訊號都先更新 JSON，再查永豐 2 當下 TMF 實際庫存並送出與目標的差額；U 由 `EF_MORNING_WEEKEND_HEDGE_UNIT` 設定。即使原始淨方向超過 ±1，也不會因超限而跳過下單。01:00 清倉仍固定以 0 為目標。
- 啟動前、停機期間與等待重設期間已收到的訊號不補單。

| 券商目前庫存 | 策略最終口數 | 本次委託 |
|---|---|---|
| 0 | +1 | 買進1口 |
| +2 | +1 | 賣出1口 |
| -1 | +1 | 買進2口 |
| +1 | -1 | 賣出2口 |
| +1 | +1 | 無需下單 |

## 重啟與送單狀態

每筆新 EF 訊號都先更新並計算 JSON 淨方向與兩種否決者，再決定 -1/0/+1 目標，乘上 `EF_MORNING_WEEKEND_HEDGE_UNIT` 後查券商 TMF 庫存、計算差額並送出一筆 IOC 委託。不掃描、不等待前一筆委託狀態，也不因中斷記錄鎖定新訊號。更新部位、否決者與訊號進度會在券商操作前一起保存。

送單前將 positions、新訊號進度、目前庫存、最終口數與預計委託一起保存。一般委託只送一次，不查成交、不重試。如果程式在送單途中中斷，該筆標記為 `interrupted_no_retry`，後續新訊號仍照常以當下券商庫存重新計算。

首次升級 v4 時，從既有 `day_signal_positions` 快照遷移一次到 `positions`，保留當日部位，不回放 CSV。遷移後刪除 `day_signal_positions`；每次存檔也移除 `source.positions`、`source.net_position`，避免保存重複且可能過期的部位快照。`source.last_signal` 與該筆訊號明細仍保留作為處理進度；手動修改策略部位時使用最外層 `positions`，並同步核對兩種否決清單。

升級舊版狀態時，若缺少否決清單，會暫時將目前持 `-1` 的策略視為多單否決者、持 `+1` 的策略視為空單否決者；下次 05:05 重設後，兩種否決者只由新的 `0→-1` 或 `0→+1` 訊號建立。服務重啟本身不會補送舊訊號或立即重算委託，仍須等新訊號。

## 手動修改

1. 停止 `ef-morning-weekend-hedge-strategy` 服務。
2. 備份 `runtime/live_state.json`，核對券商實際部位及委託紀錄。
3. 調整 `positions` 中對應策略的 -1 / 0 / 1，必須保留全部13個策略。若調整到 `-1` 或 `+1`，或從這些部位移開，也須同步核對 `long_veto_strategies` 與 `short_veto_strategies`；各清單只能列出目前持有對應部位的策略，且不可重複。
4. 啟動服務，JSON 會直接沿用；修改檔案本身不會觸發補單。

請勿在服務運行中改檔，程式不會即時重新載入，並可能覆寫人工修改。

## 帳戶、部署及紀錄

只使用 API_KEY2／SECRET_KEY2，通知使用 DISCORD_EF_CLAMP_WEBHOOK_URL。PERSON_ID2／CA_PATH2 可覆寫憑證，EF_HEDGE_ACCOUNT_ID 可核對帳號。`EF_MORNING_WEEKEND_HEDGE_UNIT` 是下單倍數，允許 1～20，預設 1；策略方向永遠是 -1／0／+1，最終口數才乘上 UNIT。合約 TMFR1、市價 MKT、IOC、Auto。EF_HEDGE_SIGNAL_CSV／EF_HEDGE_CALENDAR_PATH 可覆寫來源與日曆。

重新建置並只重啟此服務：

```sh
docker compose up -d --build --no-deps --force-recreate ef-morning-weekend-hedge-strategy
```

啟動通知須顯示 `json-positions-v7-symmetric-veto`。目前服務使用 Shioaji `simulation=True` 模擬帳戶；啟動及每日 08:35 查詢 TMF 庫存並通知，08:35 查詢只讀不送單。同一天 08:35 後啟動時，啟動查詢視為當日查詢。`python monitor_and_trade.py` 與 `--once` 都不可拿來當測試。

- runtime/live_state.json：positions、long_veto_strategies、short_veto_strategies、訊號進度、單次委託與重設狀態，原子保存。
- records/live_order_attempts.csv：委託嘗試及回應。
- records/live_events.csv：重設與異常事件。
- records/notifications.jsonl：通知紀錄。
- runtime/monitor.lock：避免同目錄多個服務並行。

測試採模擬券商，不連線實單：

```sh
python -m unittest discover -s tests -v
```

backtest.py 為理想成交回放，不模擬本版 JSON 手動修改、拒單或重設阻擋。

## 模擬帳戶通知

正式入口目前使用 API_KEY2 Shioaji 模擬帳戶。Discord 通知策略開始、訊號委託及 01:00 清倉。每筆訊號通知區分子策略的進出場訊號、13 策略合計後的帳戶目標、券商目前庫存與本次預計下單。若未取得券商庫存或委託計畫，預計下單顯示「尚未確認」，不顯示「無需下單」。送單失敗或結果不明時，通知人工核對模擬帳戶委託及庫存；委託已送出不表示已成交。
