# tv_doc layout

正式策略目前只保留「H 加碼減碼」與「反向護欄」需要的資料。

## 正式會用

```text
h_trade.csv
h_position_size_state.json
h_mdd_position_size.md
h_loss_guard_state.json
h_loss_guard_strategy.md
webhook_data_1min.csv
webhook_data_3min.csv
webhook_data_5min.csv
webhook_data_10min.csv
webhook_data_15min.csv
mxf_value.csv
priceUp.json
```

## 已封存

研究、回測、護欄、TT/MXF、TradingView 手動匯出資料都放在：

```text
tv_doc/archive_research_2026-05-15/
```

目前正式流程不會讀 `tradingview_mxf_1min.csv`；它已封存，只作為研究資料保留。
