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

目前只有 Discord 模擬，不會查詢或修改永豐部位，也不會送實單。這很重要，因為它與
H3+EF 策略同時運作時，兩套策略不能直接爭用同一個實際帳戶淨部位。

## 環境設定

沿用 `../.env`。專屬 webhook 未設定時會退回既有 MXF 通知 webhook。

```dotenv
DISCORD_EF_STRONG_WEBHOOK_URL=
EF_STRONG_MIN_GROUP_NET=2
EF_STRONG_POSITION_UNIT=1
EF_STRONG_POLL_SECONDS=2
EF_STRONG_SIMULATE_ON_START=false
```

`EF_STRONG_SIMULATE_ON_START=false` 時，首次啟動只重建目前狀態，不假裝在歷史價格成交；
收到下一批新訊號後才產生模擬調整。

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
