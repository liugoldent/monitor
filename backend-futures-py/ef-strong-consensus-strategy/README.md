# 純 EF 強共識策略

這是一套與 `h3-ef-012-strategy` 並存的獨立研究策略。它完全不讀取 H、H3
方向或 H3 交易紀錄，只使用十二套 E/F 策略目前的淨部位。

## 規則

- `E_net >= 2` 且 `F_net >= 2`：持有 TMF 多 `U` 口。
- `E_net <= -2` 且 `F_net <= -2`：持有 TMF 空 `U` 口。
- 其他情況：空手。

預設強共識門檻為 2，`U=1`。門檻和口數可透過環境變數調整，但不建議只因單月
回測結果任意提高口數。

## 資料與輸出

策略輪詢 `../tv_doc/six_strategy_signal_events.csv`，所以不需要建立第二個 Telegram
連線，也不依賴 `h3-ef-012-strategy` 的 H 或 EF 狀態檔。

- `records/ef_strong_position.json`：目前 E/F 淨部位與策略目標。
- `records/ef_strong_trade.csv`：獨立交易分析紀錄；損益已乘上實際口數與每點 10 元。
- `records/ef_strong_decisions.csv`：每批新訊號的判斷紀錄。
- `runtime/ef_strong_state.json`：來源 CSV 讀取進度與最後模擬目標。

預設為 Discord 模擬；開啟實單後會用 `API_KEY2` / `SECRET_KEY2` 查詢第二帳戶的
TMF 實際淨部位，並以 IOC 市價單調整到策略目標。

## 環境設定

沿用 `../.env`。Webhook 只使用這個策略的專屬變數，不會退回其他通知頻道。

```dotenv
DISCORD_EF_STRONG_WEBHOOK_UTL=
EF_STRONG_MIN_GROUP_NET=2
EF_STRONG_POSITION_UNIT=1
EF_STRONG_POLL_SECONDS=2
EF_STRONG_SIMULATE_ON_START=false

# 預設 false；確認下列實單設定後才可設 true。
EF_STRONG_ENABLE_ORDERS=false
API_KEY2=
SECRET_KEY2=
PERSON_ID=
CA_PATH=
```

`EF_STRONG_SIMULATE_ON_START=false` 時，首次啟動只重建目前狀態，不假裝在歷史價格成交；
收到下一批新訊號後才產生模擬調整。實單模式不受這個模擬選項影響，啟動時會立即核對第二帳戶的
TMF 部位並調整到當前目標。

若永豐下單失敗，同一目標不會自動重送。Discord 會立即通知第 1 次，之後每 60 秒提醒，
總共最多 5 次後停止，請人工下單。只有策略目標部位改變後，程式才會嘗試新的委託。

## 測試與啟動

```bash
cd /Users/kt/Desktop/self/monitor/backend-futures-py/ef-strong-consensus-strategy
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python backtest.py
../.venv/bin/python monitor_and_trade.py
```

## 研究狀態

以訊號前最近一筆可用的一分鐘收盤價重建，雙方至少淨 2 票的版本在 2026 年 7 月與
8 月可定價區間，都比只看 E/F 正負號的弱共識版本有更小的回吐與最大回撤。不過這仍
是很短的樣本，而且部分訊號缺少五分鐘內可用價格，因此目前只能作為前測策略，不能
宣稱已通過獨立驗證。
