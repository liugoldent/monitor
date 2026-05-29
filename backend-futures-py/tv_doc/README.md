# tv_doc layout

`tv_doc` 根目錄只保留正式流程會直接讀寫的核心資料。
策略自己的狀態、通知紀錄、研究輸出與備份檔分到子目錄，避免和 H 進出場、webhook K 線資料混在一起。

## 根目錄：正式資料

```text
h_trade.csv
mxf_value.csv
priceUp.json
webhook_data_1min.csv
webhook_data_3min.csv
webhook_data_5min.csv
webhook_data_10min.csv
webhook_data_15min.csv
h_position_size_state.json
h_position_size_events.csv
h_position_size_strategy.md
README.md
```

注意：`auto_trade.py` 目前會使用 `h_position_size_state.json`，不要移動。

## strategy_state：策略狀態

正式或觀察中策略的 state json 放這裡。

```text
strategy_state/
```

## strategy_alerts：策略通知紀錄

正式或觀察中策略寫出的 alert csv 放這裡。

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

已合併或已停用策略的舊 state/alert 放這裡，只留作參考。

```text
inactive_strategy_state/
```

## archive_research_*

較早期的研究、回測、手動匯出資料已封存在 `archive_research_2026-05-15/` 與 `archive_research_2026-05-19/`。
