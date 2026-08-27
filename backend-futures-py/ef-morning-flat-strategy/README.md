# EF Morning Flat Strategy

這是一個獨立的純 EF 影子策略，用來觀察「不抱過早晨休盤」的實際表現。
它不會查詢券商部位、不會連接下單 API，也不會送出實單。

## 規則

- 讀取 `../tv_doc/six_strategy_signal_events.csv` 的12套 E/F 子策略訊號。
- 一般訊號以本機實際收到的 `received_at` 為準，影子成交價採「收到訊號後
  嚴格下一分鐘的1分K開盤價」。
  例如 `08:45.xx` 訊號使用 `08:46` 的 Open。
- 每個有夜盤資料的交易日，在 `04:59` 使用 `04:59` 那根1分K開盤價清空所有影子部位。
- `08:45` 不會自動恢復清倉前的舊部位。
- 只有 `08:45` 後新收到的進場或反轉訊號才會重新建倉；舊部位的出場訊號不會讓影子倉復活。
- `13:45～15:00` 不清倉，部位正常延續。

## 啟動

Windows PowerShell：

```powershell
cd C:\path\to\monitor\backend-futures-py\ef-morning-flat-strategy
python monitor_and_trade.py
```

macOS/Linux：

```bash
cd /path/to/monitor/backend-futures-py/ef-morning-flat-strategy
../.venv/bin/python monitor_and_trade.py
```

可選環境變數：

```text
EF_MORNING_FLAT_POLL_SECONDS=2
DISCORD_EF_MORNING_FLAT_WEBHOOK_URL=...
```

如未設定專用 Discord webhook，會回退使用
`DISCORD_MXF_ALERT_WEBHOOK_URL`。

## 記錄

程式啟動後會建立：

- `records/ef_morning_flat_position.json`：原 EF 與影子部位快照。
- `records/ef_morning_flat_decisions.csv`：訊號判定與04:59定時清倉。
- `records/ef_morning_flat_shadow_trade.csv`：逐策略影子進出、成交價與已實現損益。
- `runtime/ef_morning_flat_state.json`：重啟用狀態。

`records/` 與 `runtime/` 皆已加入 `.gitignore`，不會把實際監控資料提交到 Git。

## 回測

```powershell
python backtest.py --start "2026-08-01 00:00:00" --one-way-cost 20
```

輸出會同時列出原純 EF 跨休盤，以及 `04:59清倉、08:45等新訊號`
兩種結果。

## 測試

```powershell
python -m unittest discover -s tests -v
```
