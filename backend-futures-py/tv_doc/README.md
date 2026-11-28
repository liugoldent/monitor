# tv_doc layout

`tv_doc` 根目錄只保留正式流程會直接讀寫的核心資料。
策略自己的狀態、通知紀錄、研究輸出與備份檔分到子目錄，避免和 H 進出場、webhook K 線資料混在一起。

## 根目錄：正式資料

```text
h_trade.csv
h2_trade.csv
mxf_value.csv
priceUp.json
webhook_data_1min.csv
webhook_data_3min.csv
webhook_data_5min.csv
webhook_data_10min.csv
webhook_data_15min.csv
README.md
```

`h_trade.csv`是舊H交易流程留下的歷史交易資料，目前週一啟動鏈與H3＋EF混合策略都不會讀取；暫時保留供績效分析。

`h2_trade.csv` 儲存 H2 單口策略的逐筆進出場，欄位與 `h_trade.csv` 相同。`pnl` 是已平倉單口毛損益（台幣，每點 10 元）。歷史取自 TradingView 匯出，後續由 Telegram relay 按可信 H2 訊號更新，取收到訊號當時已收到的 `MXF1!` 一分鐘收盤價，K 棒時間距訊號最多 120 秒。反手同時記平倉和新進場，重複方向只保留訊號原始紀錄。缺少可用行情時，保留原始訊號並在 relay 啟動或收到下一筆 H2 訊號時重試。

`h2_trade.csv` 由 Git 追蹤，新部署可直接沿用已提交的交易歷史。若需重建，可使用已有的原始訊號及行情。需要更早的歷史時，應另外匯入已配對的 TradingView 交易。

## strategy_state：策略狀態

仍在使用的策略 state json 放這裡；本次已移除停用H觀察策略的狀態。

```text
strategy_state/
```

## strategy_alerts：策略通知紀錄

仍在使用的策略寫出的 alert csv 放這裡；本次已移除停用H觀察策略的通知紀錄。

```text
strategy_alerts/
```

## research_outputs：研究與回測輸出

`backend-futures-py/research/` 內的研究腳本輸出放這裡。

```text
research_outputs/
```

## backups：備份與補資料副本

例如 `mxf_value.backfilled.csv` 這類補資料副本放這裡。

```text
backups/
```

## inactive_strategy_state：已停用策略狀態

停用策略的舊 state/alert 已清除；目錄若為空可忽略。

```text
inactive_strategy_state/
```

## archive_research_*

較早期的研究、回測、手動匯出資料已封存在 `archive_research_2026-05-15/` 與 `archive_research_2026-05-19/`。

# mxf value
(坦克、游擊、炮灰、游擊平均）
