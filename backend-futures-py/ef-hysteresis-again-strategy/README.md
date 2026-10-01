# EF Hysteresis Again（API_KEY 模擬）

這套策略使用原永豐 1 的 `API_KEY` / `SECRET_KEY`、`PERSON_ID` 與憑證，
以 Shioaji `simulation=True` 向模擬環境的 TMF 近一合約送市價 IOC 差額單。它讀取共用的
`tv_doc/six_strategy_signal_events.csv`，但擁有自己的 runtime、records、鎖檔與
Discord webhook。啟動時先查券商庫存，既有訊號僅用於重建當日策略目標，
不補送舊單；後續每筆新訊號都重新查庫存並調整到目標。委託失敗或回傳不明時，
同一筆不重送，下一筆新訊號重新查庫存。01:00 依時鐘清倉。
啟動查倉結果也會印在 Live Docker logs；若登入或查倉逾時，先保持未讀取新訊號，
記錄失敗並等待 30 秒，再退出交由 Docker 重啟，不在同一程序反覆建立 Shioaji 連線。

## 固定規則

- 進場門檻固定為 E、F 各自同向淨部位 2；續抱門檻固定為 1。
- 01:00 目標清為零並查券商庫存送差額單；01:00～08:45 只更新原始 E/F 狀態。
- 08:45 後第一筆 EF 訊號到達時，先檢查該訊號發生前的 E/F 狀態。
- 若此前已是 `E >= +2` 且 `F >= +2`，鎖住多方，不因尾端訊號追多。
- 多方鎖定後必須先出現任一組 `< +2`；之後重新達到兩組皆 `>= +2` 才做多。
- 空方規則對稱：若此前已是 `E <= -2` 且 `F <= -2`，必須先出現任一組 `> -2`；之後重新達到兩組皆 `<= -2` 才做空。
- 已持多單時，若某個策略的追蹤部位由 `0→-1`，當筆目標立即設為空手並對帳下單，即使 E/F 原本仍符合續抱條件。`1→0` 不額外出場；空單規則不變。下一筆訊號仍用原本的進場條件判斷，這筆反向訊號不建立跨訊號的禁止進多狀態。
- 啟動會重播當日已收到的 CSV 訊號以重建策略狀態，但不為這些舊訊號下單。
- 每日 08:35 起查詢一次 API_KEY 的 TMF 淨部位並通知；只查詢，不送委託。同日 08:35 後啟動時，啟動查倉視為當日查倉。

下單口數使用原帳戶的 `EF_HYSTERESIS_MORNING_FLAT_POSITION_UNIT`，目前為 1。
新委託稽核寫在 `records/live_order_attempts.csv`。API 回傳代表已呼叫送單，
不代表成交確認；請以券商庫存及委託狀態核對。

Discord 使用：

```dotenv
DISCORD_EF_HYSTERESIS_AGAIN_WEBHOOK_URL=
EF_HYSTERESIS_AGAIN_POLL_SECONDS=2
EF_HYSTERESIS_AGAIN_RECONNECT_TIMEOUT_SECONDS=120
```

永豐連線短暫中斷時先由 SDK 自動重連；若連續 120 秒未恢復，策略程序退出，
由 Docker Compose 的 `restart: unless-stopped` 拉起。重連期間暫停處理訊號；
恢復後只更新斷線期間的策略狀態，不補送舊訊號委託。重啟後也會先查券商庫存，
不補送啟動前訊號。可用上述環境變數調整等待秒數。

啟動：

```powershell
docker compose up -d --build ef-hysteresis-again-strategy
```

測試：

```powershell
python -m unittest discover -s tests -v
```
