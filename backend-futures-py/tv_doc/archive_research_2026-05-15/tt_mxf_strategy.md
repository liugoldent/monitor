# TT/MXF Strategy

This strategy is the only active strategy in `webhook_server.py`.
It currently sends alerts only. It does not place real orders.

## Inputs

- `webhook_data_1min.csv`
  - 1-minute OHLC data
  - `tt_short`
  - `tt_long`
  - `BBR`
- `mxf_value.csv`
  - `tx_bvav`
  - `mtx_bvav`
  - `mtx_bvav_avg`
  - `signal`
  - `trend`

## Core Idea

- `TT` defines direction
- `MXF` confirms force
- `BBR` is only a filter

This is intentionally conservative:

- no chasing inside the TT range
- no single-bar confirmation
- no aggressive scaling
- exit fast when the structure weakens
- the current calibration only enables short entries on this sample set
- fixed stop loss / take profit are used to avoid runaway losses

## Entry Rules

### Long

- long entries are currently disabled in the calibrated live rule set
- long exits are still allowed if a position already exists

### Short

- current close is below both `tt_short` and `tt_long`
- previous close is also below both
- latest 2 MXF rows are both `signal=bear` and `trend=death`
- `BBR` is not breaking upward
- `BBR` is capped to a conservative upper bound
- current close must stay at least a small buffer below the TT band

## Exit Rules

### Long exit

- close returns inside the TT band
- or MXF flips to bearish
- or close falls back below the TT band
- or the position reaches the fixed stop loss / take profit threshold

### Short exit

- close returns inside the TT band
- or MXF flips to bullish
- or close rises back above the TT band
- or the position reaches the fixed stop loss / take profit threshold

## Execution Flow

1. Webhook writes the latest 1-minute candle to `webhook_data_1min.csv`
2. `_apply_tt_mxf_strategy()` reads the latest 2 price rows and latest 2 MXF rows
3. The strategy checks entry or exit conditions
4. The strategy writes a pending/position state file to avoid duplicate triggers
5. The strategy sends Discord notifications and writes the trade/state logs only

## State Files

- `tt_mxf_state.json`
  - current position
  - pending action
  - pending timestamp
- `tt_mxf_trade.csv`
  - trade log for this strategy
  - this is an alert log, not an execution log

## Files No Longer Needed by Removed Strategies

These were removed because the old BBR-based strategies are no longer called from `webhook_server.py`:

- `bb_trade.csv`
- `bbr960_state.json`
- `bbr960_trade.csv`
- `bbr_wave_state.json`
- `tt_bbr_state.json`
- `tt_bbr_trade.csv`

`h_trade.csv` is intentionally kept because other modules still reference it.
