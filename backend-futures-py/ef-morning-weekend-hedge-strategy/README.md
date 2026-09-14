# 永豐 2：純 EF＋04:59 清倉

永豐第二帳戶改為純 EF 跟單：**04:59 清倉，08:45 後等待新訊號入場**。沿用本目錄名稱與第二組 API 金鑰。

## 規則

- 支援 `-1 → 0`、`1 → 0`、`0 → 1`、`0 → -1`、`1 → -1`、`-1 → 1`；依新訊號的部位變化送差額委託。已有多 1 口反轉空 1 口時，差額為賣 2 口；清倉後空手子策略收到平倉訊號不會反向開倉。
- 十二個 E/F 子策略預設每策略 1 口微台；收到新訊號才查券商實際庫存，以實際庫存加該筆差額決定委託目標。不使用強共識門檻，也不做反向避險。
- 每個有效夜盤 **04:59** 由本機時鐘將第二帳戶目標設為 **0**，查券商實際部位並下差額單。此步驟不依賴 CSV 或新訊號。
- 清倉後各子策略歸零；04:59 至下一個實際開市日 08:45 不建立部位，週末與連假保持空手。
- **08:45 不自動恢復舊部位**。之後各子策略收到新訊號，只更新該子策略。例如昨晚 E 多 7，開市只有一個子策略新多訊號，就只建立該訊號的 1 口，不恢復昨晚 7 口。空手子策略收到平倉訊號仍是 0。
- 13:45～15:00 保留部位；休市期間收到的訊號不補單，等可交易時段的新訊號。
- 每次啟動／重啟只記錄當下訊號檔起點，不登入券商、不下單、不清倉、不重放舊訊號或補到舊目標。舊委託狀態保留稽核後不再追補。04:59 時段啟動仍執行定時清倉。
- 錯過 04:59 清倉時段，會在下一個可交易時段先清倉，再等待清倉確認之後的新訊號。清倉失敗或結果不明時不入場。

## 訊號與帳戶

讀取 `../tv_doc/six_strategy_signal_events.csv`，僅採本機 `received_at`。訊號以每個子策略的 `new_position` 更新；不讀群益庫存快照、不納入 H3 或第一帳戶。第二帳戶須專用；訊號依時間及 CSV 列順序逐筆執行，每筆確認後保存進度，不把同一輪收到的多筆訊號合併成最終淨額。同一次執行逐筆保存完成進度。每次重啟建立新的訊號起點，停機期間的訊號不補單；僅在新訊號或定時清倉時呼叫券商。相同目標不再每五分鐘登入、查庫存或發送委託結果通知；本機訊號仍每秒檢查。若人工改動券商部位，將於下次新訊號或清倉時檢查。

只使用 `API_KEY2` / `SECRET_KEY2`，沿用登入後的 `api.futopt_account`，不會退回第一組金鑰。`EF_HEDGE_ACCOUNT_ID` 改為選填，填入時仍核對帳號。仍使用 `DISCORD_EF_hedge_WEBHOOK_URL` 專用通知。

下單與第一帳戶共用 `shioaji_tmf_target.py`：自動選擇 `api.Contracts.Futures.TMF.TMFR1` 近月合約，查實際 TMF 淨部位，以市價 IOC、Auto 送差額單，檢查委託回報及成交後部位，最後登出。`EF_HEDGE_CONTRACT` 不再使用，無須手填月份。近月指向改變時不自動搬移舊月份持倉；發現其他月份、多空雙邊庫存或未結委託仍會停止並通知。永豐 2 在實際送單前另檢查時間，避免登入或查詢延遲跨過 04:59 入場截止時間。

## 設定與啟動

沿用上層 `.env`，環境變數優先。監控入口固定使用第二帳號實單。

```dotenv
API_KEY2=第二組金鑰
SECRET_KEY2=第二組密鑰
PERSON_ID=憑證身分證字號
CA_PATH=/absolute/path/Sinopac.pfx
DISCORD_EF_hedge_WEBHOOK_URL=專用通知網址
EF_HEDGE_SOURCE_UNIT=1
EF_HEDGE_MAX_CONTRACTS=12
```

可使用 `PERSON_ID2`／`CA_PATH2` 覆寫第二帳戶憑證；`EF_HEDGE_SIGNAL_CSV`／`EF_HEDGE_CALENDAR_PATH` 覆寫訊號及日曆。`EF_HEDGE_ACCOUNT_ID` 可選填作額外帳號核對。原有 CONTRACT、WEEKDAY_CAP、HOLIDAY_CAP、SOURCE_SNAPSHOT、ACCEPT_SIGNAL_ESTIMATE 設定不再使用。

```bash
cd backend-futures-py/ef-morning-weekend-hedge-strategy
python monitor_and_trade.py --once
python monitor_and_trade.py
```

`monitor_and_trade.py` 與 `--once` 都會連線第二帳號並可能送實單，不以 `EF_PURE_FLAT_ENABLE_ORDERS` 切換；離線測試使用注入的模擬執行器。

## 舊策略切換與失敗處理

啟動遇到舊避險版本的 runtime 狀態會停止，避免把原本的反向部位當成新策略持倉。切換時先停止舊程式、核對並平掉第二帳戶原有部位，封存原本 `runtime/live_state.json`／`shadow_state.json`，再啟動新策略。新版本保留原來的 `runtime/monitor.lock`，防止同目錄多開。

委託前保存意圖，使用 IOC 市價差額單並驗證實際淨部位。委託失敗、部分成交或中斷後，每 10 秒自動重新查詢券商庫存與未結委託，依尚未完成的訊號目標送差額單，完成後繼續下一筆，無須人工解鎖；若重啟，依使用者要求放棄舊訊號的追補，只處理新訊號。已成交的口數會計入實際庫存，不重送原始口數。未結委託或庫存異常尚未排除時，該次不送新單，之後繼續重查。04:59 清倉優先於進場重試等待；清倉失敗仍自動重試。`--retry-failed` 僅供人工對帳後立即解除等待。04:59:40 後不送夜盤委託，錯過清倉會告警並等待可交易時段。

日曆沿用 `config/calendar.json`；臨時休市須更新 `closed_dates`，取消夜盤須更新 `no_night_dates`。日期超出涵蓋範圍會停止判斷。通知採背景佇列，不阻塞下單；稽核保存在 `records/{live,shadow}_events.csv`。

## 驗證與回放

```bash
python -m unittest discover -s tests -v
python backtest.py --start '2026-09-01 08:45:00' --end '2026-09-14 13:45:00'
```

回放已改為純 EF 第二帳戶，區間開始時空手、等待後續新訊號；04:59 歸零、08:45 不恢復。訊號用嚴格下一分鐘 MXF1! Open，清倉用精確 04:59 Open，每點每口 10 元，單邊預設成本 2 點；期末持倉按最後 K 棒 Open 評價。缺少必要成交 K 棒則停止，不跳過缺口。實單即時依訊號下單，成交可能與回放代理價不同；舊避險回測數字不適用新策略。


## 共用下單、通知與紀錄（2026-09-14）

兩個 EF 策略共同使用 `../ef_trade_runtime.py` 與 `../shioaji_tmf_target.py`：

- 根據券商實際 TMF 部位計算差額，使用 TMFR1、市價 MKT、IOC、Auto；送出後回查目標部位。
- 送單前共同檢查未結束的 TMF 委託、其他月份庫存及同時持有多空庫存；真正送單前再檢查期限。
- 先以原子寫入、fsync 與 OneDrive 鎖定重試保存委託狀態。永豐 2 失敗或結果不明時自動重新對帳，逐筆依訊號補差額；第一帳號的失敗鎖定規則維持原樣。
- 永豐 2 無須使用 `--retry-failed` 才能恢復；自動重試仍會重新檢查券商委託與庫存，並記錄 `automatic_reconcile` 稽核事件。
- Discord 共用非同步佇列、10 秒逾時、NotifierBot 與長訊息分段；通知失敗不阻塞下單。訊息包含目標部位、觸發原因與已確認的實際部位／失敗結果。
- 新實單委託統一寫入各自的 `records/live_order_attempts.csv`，欄位為 timestamp、attempt_id、event、trigger、target_position、previous_position、actual_position、side、quantity、detail。
- 通知發送結果統一寫入各自的 `records/notifications.jsonl`，包含 timestamp、status、content。status 可為 sent、failed、missing_webhook、queue_full；不寫入 webhook URL 或原始網路例外。

帳號憑證、webhook 設定與策略決策各自保留。既有策略決策、影子成交、時鐘及歷史紀錄仍保留在原處；新委託稽核以共用格式為準，不將影子成交當成券商成交。

程式碼更新後須重新建置映像並重建對應服務才會套用；單純重新啟動舊映像不會載入新程式。本次離線測試不會登入券商或執行實單監控入口。

## 訊號口數

每策略預設 1 口；在該策略原部位正常跟隨的情況下：

| 訊號 | 下單 |
|---|---|
| -1 → 0 | 買 1 平空 |
| 1 → 0 | 賣 1 平多 |
| 0 → 1 | 買 1 |
| 0 → -1 | 賣 1 |
| 1 → -1 | 賣 2，平多並轉空 |
| -1 → 1 | 買 2，平空並轉多 |

04:59 已清倉的子策略收到平倉訊號，仍維持空手；收到反轉訊號則從空手建立新方向 1 口。多筆訊號逐筆處理，失敗的訊號自動重新對帳後繼續，已成交口數不重複下單。04:59 清倉優先，舊日未完成的進場訊號不在隔日補下。無指定 4 口的補倉設定。

## Windows 啟動版本確認

啟動通知會包含 `new-signals-only-v2` 與「啟動不下單、不補舊訊號」。Windows 啟動器會拉取、build，並對永豐 2 使用 `--force-recreate`，每次執行都重新建立監控起點。其他服務維持一般 Compose 啟動方式。舊通知若仍出現 `startup_reconcile`，代表實單機器未執行此版本。
