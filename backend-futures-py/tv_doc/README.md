# tv_doc layout

正式策略目前只保留「第一帳號下單」與「H 反向護欄」需要的資料。

## 正式會用

```text
h_trade.csv
h_position_size_state.json
h_profit_breakout_add_state.json
h_profit_breakout_add_alert.csv
h_reverse_guard_state.json
h_reverse_guard_alert.csv
mxf_value.csv
webhook_data_1min.csv
webhook_data_3min.csv
webhook_data_5min.csv
webhook_data_10min.csv
webhook_data_15min.csv
README.md
```

## 已封存

研究、回測、護欄、TT/MXF、TradingView 手動匯出資料都放在：

```text
tv_doc/archive_research_2026-05-15/
tv_doc/archive_research_2026-05-19/
```

目前正式流程不會讀 `tradingview_mxf_1min.csv`；它已封存，只作為研究資料保留。
舊版 `h_loss_guard_*` 護欄策略已移到 `archive_research_2026-05-19/`。
非目前 H 反向護欄正式流程需要的 md/csv/json 也已移到 `archive_research_2026-05-19/`。
