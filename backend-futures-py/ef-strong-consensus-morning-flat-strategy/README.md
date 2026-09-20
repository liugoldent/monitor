# EF Hysteresis 共識＋01:00 清倉策略

本目錄保留原 `ef-strong-consensus-morning-flat-strategy` 路徑與 Docker service 名稱，
只為延續既有 runtime、委託防重紀錄與掛載。正式策略已改為 Hysteresis；原本「進場、
退出都要求 E/F 各達門檻 2」的強共識規則不再用於實單決策。

## 2026-09-18：沿用 demo 單次送單方式（目前行為）

- 使用主帳號 `API_KEY` / `SECRET_KEY`，委託格式維持 TMFR1、市價 MKT、IOC、Auto，`place_order(..., timeout=0)`。
- 新訊號只查當下券商庫存，以「最終口數 − 券商庫存」計算本次差額；送單後不輪詢成交、不重送。
- API 返回後，先保存委託 ID 與送單紀錄，再保存監控狀態 `submitted`，最後登出。登出中斷不會抹掉已保存的送單結果。
- API 返回不代表券商受理或成交，通知標示「API已返回，未回查成交」，不更新「已確認券商部位」。庫存原本已達目標而未下單時，可以記錄當下查到的庫存。
- API 返回前結果不明時，本筆不重送；後續新訊號不掃描舊委託，直接重新查庫存、計算差額。同一訊號仍只嘗試一次。
- 即時通知固定列出：EF 策略與訊號、目前券商庫存、本次預計下單、收到策略後最終口數、實際送單結果。
- 以下 2026-09-15 的送單後回查說明是歷史行為，與本節衝突時以本節為準。

## 2026-09-15：逐筆目標對帳與成交一致性（歷史）

- 每筆訊號依接收順序更新十二策略狀態、計算目標，再查永豐實際 TMF 庫存，以「目標 − 實際」送差額。兩筆連續訊號可以依序進場、出場；不合併訊號，也不以策略舊目標代替實際庫存。
- 庫存 `None`、無效口數或缺合約代碼是查詢異常，不能視為空手。送單前驗證與差額計算使用同一份庫存快照。
- `runtime` 的 `broker_reconciliation.pending` 在送單前落盤，回應後保存委託 ID。成功須同時確認委託結束、成交口數與庫存一致、實際庫存達到目標。
- 前筆未確認時，後續新訊號仍計算目標，但先依委託 ID 查證前筆。委託未結束或成交尚未反映於庫存時不送單、不宣稱空手；下一筆新訊號再核對。只有目標相同不會阻止新的庫存核對。
- 若送單回應遺失，無法取得／唯一查到委託 ID，保留跨重啟的未確認狀態並通知人工核對；不得直接刪除 `pending` 來繞過。此規則優先於下方舊版「不鎖單」描述。
- 不自動重送失敗訊號或補足部分成交；後續新訊號核對前筆已結束且庫存一致後，按最新目標重新計算差額。
- 通知以「策略目標部位」表示意圖；只有券商核對成功才報已確認實際部位。拒單原因保留在委託稽核中；`broker_reconciliation` 保留前筆委託及成交核對資料。
- 本機流程無法保證券商永遠即時回報或成交；資料無法確認時採取不送新單、明確通知的處理方式。另有程式或人工同時交易同帳戶時，也可能需要人工核對庫存差異。

券商介面依據：[更新委託狀態](https://sinotrade.github.io/tutor/order/UpdateStatus/)、[查詢庫存](https://sinotrade.github.io/tutor/accounting/position/)。

這是一套獨立的 EF 衍生策略。它讀取十三套 E/F 策略訊號，但只維護一個
組合部位；不會把十三套訊號口數直接相加。E 組七套、F 組六套。

正式入口 `monitor_and_trade.py` 固定以實單模式啟動，舊的
`EF_HYSTERESIS_MORNING_FLAT_ENABLE_ORDERS=false` 設定不再停用實單。使用獨立的
`API_KEY` / `SECRET_KEY` 查詢永豐 TMF 實際淨部位、送差額 IOC 市價單；送單後不回查成交。

## 規則

```text
空手時，E 組淨部位 >= +2 且 F 組淨部位 >= +2：組合多 1 口
空手時，E 組淨部位 <= -2 且 F 組淨部位 <= -2：組合空 1 口
持有多單時，E、F 皆仍 >= +1：續抱；任一組 < +1：平倉
持有空單時，E、F 皆仍 <= -1：續抱；任一組 > -1：平倉
持倉中若兩組同時達到反方向進場門檻：直接反轉

每天 01:00 清空組合部位
08:45 不自動恢復清倉前的部位
08:45 後收到新的 E/F 訊號時，才重新計算 Hysteresis 目標
```

E 組與 F 組各包含六套既有策略。無論同向票數多高，組合最多只有一口。

## 實單訊號與本機績效紀錄

- 訊號只使用本機 `received_at`；沒有 `received_at` 的舊資料不能當成可交易事件。
- 實單模式在新訊號寫入 CSV 後立即重算完整目標、向永豐送差額單；不等下一分鐘K棒。
- 本機研究紀錄使用嚴格下一分鐘的 1 分 K Open 作為模擬價，不發 Discord 補記通知，也不代表券商成交。
- 例如 `08:45:04` 收到訊號，使用 `08:46` Open。
- 反向時在同一成交價先平舊方向，再開新方向。
- `01:00～08:45` 的訊號只更新原始 E/F 狀態，不建立組合部位。
- `13:45～15:00` 不清倉，部位正常延續。
- 01:00實單清倉由本機時鐘排程立即對帳，不等待K棒檔案寫入；K棒只負責稍後補記 shadow 成交價。
- 時鐘排程會記錄預定時間、實際觸發時間、完成時間，以及是否在01:00:30前完成；
  Discord 通知策略開始、新訊號實單執行結果與時鐘清倉；不另發 K 棒模擬清倉通知。
- 每次E/F判斷通知會列出兩組各六套策略的目前部位與加總，方便核對淨部位來源。

## EF訊號資料流

Telegram 收訊由獨立的 `telegram-signal-relay` 服務負責。它只記錄與轉發訊號，
持續將有效 E/F 事件寫入 `tv_doc/six_strategy_signal_events.csv`，不會使用券商帳戶
或送出委託；本策略只讀取這份共用事件檔。

## 目錄與輸出

程式第一次執行時會自動建立：

```text
records/ef_strong_morning_flat_position.json
records/ef_strong_morning_flat_decisions.csv
records/ef_strong_morning_flat_shadow_trade.csv
records/ef_strong_morning_flat_order_attempts.csv
records/ef_strong_morning_flat_clock_events.csv
runtime/ef_strong_morning_flat_state.json
runtime/ef_strong_morning_flat.lock
```

`records/` 保存目前組合部位、每筆判斷及影子交易；`runtime/` 保存來源 CSV
讀取進度、原始十二策略部位、組合進場價及重啟狀態。
`ef_strong_morning_flat_order_attempts.csv` 是追加式實單稽核，包含開始嘗試、成功送單
並回查、帳戶已符合目標、永豐失敗與防重送略過。

影子交易的 `pnl_points` 為指數點；`pnl_twd` 預設按 TMF 每點 NT$10 計算。

## 環境設定

沿用 `../.env`：

```dotenv
# 專用 Discord webhook（未設定才回退到既有 MXF webhook）。
DISCORD_EF_HYSTERESIS_MORNING_FLAT_WEBHOOK_URL=

# 這套策略指定使用主憑證。
API_KEY=
SECRET_KEY=
PERSON_ID=
CA_PATH=

# 正式入口固定實單；此舊開關不再控制正式入口。
EF_HYSTERESIS_MORNING_FLAT_ENABLE_ORDERS=true
EF_HYSTERESIS_MORNING_FLAT_POSITION_UNIT=1

# 回測選定：兩組各達2才進場，持倉後兩組各保留1才續抱。
EF_HYSTERESIS_ENTRY_GROUP_NET=2
EF_HYSTERESIS_HOLD_GROUP_NET=1

EF_HYSTERESIS_MORNING_FLAT_POLL_SECONDS=2
```

`EF_HYSTERESIS_MORNING_FLAT_POSITION_UNIT` 是 U，允許 1～20。Hysteresis 方向仍是
-1/0/1，永豐最終目標會縮放成 -U/0/+U；修改 U 後重啟會依新口數對帳。

## 測試

```powershell
cd C:\path\to\monitor\backend-futures-py\ef-strong-consensus-morning-flat-strategy
python -m unittest discover -s tests -v
```

## 回測

回測必須固定 `--end`，避免正式資料持續追加造成結果不可重現：

```powershell
python backtest.py `
  --start "2026-06-24 00:00:00" `
  --end "2026-08-27 16:01:00" `
  --threshold 2 `
  --hold-threshold 1 `
  --one-way-cost 2 `
  --point-value 10
```

- `--point-value 10`：TMF。
- `--point-value 50`：MXF。
- `--one-way-cost` 以每次單邊調整的指數點估計，毛損益仍會分開保留。

## 啟動監控

```powershell
python monitor_and_trade.py
```

正式入口固定為實單模式。啟動不補單，等待啟動後的新 EF 訊號；01:00時鐘清倉獨立執行。每筆新訊號依券商實際庫存調整到策略目標，已符合目標則不送單。同一筆訊號不重送；下一筆新訊號即使目標相同，也照常查庫存並處理。失敗或中斷只留下紀錄與通知，不鎖住新訊號或清倉。

## 上線門檻

目前歷史樣本仍短。建議固定規則後先累積至少 60 個全新交易日或 50 個全新已平倉腿，
再檢查扣除實際成本後的 Profit Factor、逐分鐘最大回撤及訊號漏接狀況；實單仍固定最多1口。


## 共用下單、通知與紀錄（2026-09-14）

Hysteresis 使用 `../ef_trade_runtime.py` 與 `../shioaji_tmf_target.py` 的低階庫存、合約及建單工具；純 EF 的差額送單規則另見該策略 README：

- 根據券商當下 TMF 淨部位計算差額，使用 TMFR1、市價 MKT、IOC、Auto；送出後不回查成交。
- 不查舊委託、不以 `broker_reconciliation.pending` 擋住新訊號；每筆新訊號都以當下庫存重算。
- 先以原子寫入、fsync 與 OneDrive 鎖定重試保存委託狀態。失敗或結果不明時，不自動重送。
- 一般訊號與清倉失敗皆轉成 `failed_no_retry`，中斷的舊委託轉成 `interrupted_no_retry`，保留 `last_unconfirmed_attempt` 供查核，不鎖住後續新訊號。清倉異常另外保留 `manual_flat_required` 並通知早上人工處理，不補送舊清倉單。
- 不需人工解除失敗鎖定；`--retry-failed` 僅保留舊版相容。新訊號仍會查券商實際庫存並調整到目標，券商查詢或送單異常會使該次失敗，但不會在本機建立持續鎖定。
- Discord 共用非同步佇列、10 秒逾時、NotifierBot 與長訊息分段；通知失敗不阻塞下單。訊息包含策略訊號、券商庫存、預計下單、最終口數與實際送單結果。
- 新實單委託統一寫入各自的 `records/live_order_attempts.csv`，欄位為 timestamp、attempt_id、event、trigger、target_position、previous_position、actual_position、side、quantity、detail。
- 通知發送結果統一寫入各自的 `records/notifications.jsonl`，包含 timestamp、status、content。status 可為 sent、failed、missing_webhook、queue_full；不寫入 webhook URL 或原始網路例外。

帳號憑證、webhook 設定與策略決策各自保留。既有策略決策、影子成交、時鐘及歷史紀錄仍保留在原處；新委託稽核以共用格式為準，不將影子成交當成券商成交。

程式碼更新後須重新建置映像並重建對應服務才會套用；單純重新啟動舊映像不會載入新程式。本次離線測試不會登入券商或執行實單監控入口。
