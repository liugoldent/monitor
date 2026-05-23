# MA960 Flow Strategy

## Purpose

This folder contains the standalone MA960 + MXF flow strategy draft. It is separate from the H guard strategies and is not wired into `webhook_server.py` yet.

## Idea

Long continuation setup:

- `Close` is above `MA_960`.
- `Close - MA_960 <= 60`, so price is riding close above MA960.
- `MA_960` has risen over the last 15 one-minute bars.
- `tx_bvav > 0`, meaning foreign/institutional flow is long.
- `mtx_bvav > 0`, meaning dealer/proprietary flow is long.

Setup labels:

- `super_long`: big money long, retail (`mtx_tbta`) short.
- `shakeout_long`: big money long, retail also long.

## Files

- `strategy_ma960_flow_draft.py`: live alert draft. It reads `../../tv_doc/webhook_data_1min.csv` and `../../tv_doc/mxf_value.csv`, then writes local alert/state files in this folder.
- `research_ma960_flow_strategy.py`: backtest and optimizer script.
- `ma960_flow_strategy_research.md`: latest generated research report.
- `ma960_flow_strategy_trade.csv`: latest practical draft trade list.

## Current Practical Draft

```text
dist_to_ma960 <= 60
MA960 slope over 15 minutes > 0
take profit = 120 points
stop loss = 60 points
max hold = 30 minutes
trend gold is not required
```

Latest research summary:

```text
trades = 34
points = +748
win rate = 50.0%
max loss = -79
max drawdown = 365
```

## Run

From repo root:

```bash
backend-futures-py/.venv/bin/python backend-futures-py/strategies/ma960_flow/research_ma960_flow_strategy.py
```

Manual one-shot live evaluation:

```bash
backend-futures-py/.venv/bin/python backend-futures-py/strategies/ma960_flow/strategy_ma960_flow_draft.py
```

## Status

Research/draft only. Do not assume it is live unless `webhook_server.py` imports and calls `evaluate_ma960_flow_strategy()`.
