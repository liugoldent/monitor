# Six Strategy x MXF Value Validation - 2026-06-29

## Method
- Used `message_time` from `six_strategy_signal_events.csv`; 8 untimed rows were excluded.
- `reverse` was treated as an exit of the old side plus entry of the new side.
- Entry/exit price uses the closest previous `webhook_data_1min.csv` `Record Time` close within 5 minutes.
- `mxf_value.csv` alignment uses the closest previous row within 5 minutes. Missing values are treated as unavailable, not as passed/failed signals.
- Results are in MXF points; `pnl_twd_x10` assumes 10 TWD per point and excludes commissions/slippage.

## Data Coverage
- Timed signal events: 21; untimed events: 8.
- Closed trades reconstructed: 9; open positions: 2; unmatched timed exits: 1.
- Closed-trade entry rows with usable `mxf_value`: 1/9. Exit rows with usable `mxf_value`: 2/9.
- Important gap: `mxf_value.csv` has no rows on 2026-06-25 and resumes only at 2026-06-26 20:25:23 after 2026-06-24 12:17:00.

Recent `mxf_value` row counts:
| date       | mxf_rows |
| ---------- | -------- |
| 2026-06-16 | 1140     |
| 2026-06-17 | 1140     |
| 2026-06-18 | 1140     |
| 2026-06-19 | 1101     |
| 2026-06-22 | 61       |
| 2026-06-23 | 128      |
| 2026-06-24 | 513      |
| 2026-06-26 | 215      |
| 2026-06-27 | 300      |
| 2026-06-29 | 4        |

Largest `mxf_value` gaps:
| from                | to                  | gap             |
| ------------------- | ------------------- | --------------- |
| 2026-02-13 09:02:00 | 2026-02-23 08:45:00 | 9 days 23:43:00 |
| 2026-04-02 23:59:00 | 2026-04-07 08:45:00 | 4 days 08:46:00 |
| 2026-05-01 13:43:01 | 2026-05-04 08:45:00 | 2 days 19:01:59 |
| 2026-02-27 20:47:00 | 2026-03-02 08:45:00 | 2 days 11:58:00 |
| 2026-05-08 22:34:00 | 2026-05-11 08:45:00 | 2 days 10:11:00 |
| 2026-06-19 23:20:00 | 2026-06-22 08:45:00 | 2 days 09:25:00 |
| 2026-06-24 12:17:00 | 2026-06-26 20:25:23 | 2 days 08:08:23 |
| 2026-01-17 04:59:00 | 2026-01-19 08:45:00 | 2 days 03:46:00 |

## Baseline And Filter Results
| rule                    | trades | total_points | total_twd_x10 | avg_points | win_rate | profit_factor | max_drawdown_points | best    | worst    |
| ----------------------- | ------ | ------------ | ------------- | ---------- | -------- | ------------- | ------------------- | ------- | -------- |
| baseline_all            | 9      | -224.00      | -2240.00      | -24.89     | 44.4%    | 0.90          | -1203.00            | 1199.00 | -1196.00 |
| mxf_available           | 1      | 31.00        | 310.00        | 31.00      | 100.0%   | inf           | 0.00                | 31.00   | 31.00    |
| signal_align            | 0      | 0.00         | 0.00          |            |          |               | 0.00                |         |          |
| trend_align             | 1      | 31.00        | 310.00        | 31.00      | 100.0%   | inf           | 0.00                | 31.00   | 31.00    |
| both_align              | 0      | 0.00         | 0.00          |            |          |               | 0.00                |         |          |
| signal_or_trend_align   | 1      | 31.00        | 310.00        | 31.00      | 100.0%   | inf           | 0.00                | 31.00   | 31.00    |
| not_against_signal      | 1      | 31.00        | 310.00        | 31.00      | 100.0%   | inf           | 0.00                | 31.00   | 31.00    |
| mtx_bvav_sign_align     | 0      | 0.00         | 0.00          |            |          |               | 0.00                |         |          |
| mtx_bvav_avg_sign_align | 0      | 0.00         | 0.00          |            |          |               | 0.00                |         |          |
| tx_bvav_sign_align      | 1      | 31.00        | 310.00        | 31.00      | 100.0%   | inf           | 0.00                | 31.00   | 31.00    |
| tbta_sign_align         | 1      | 31.00        | 310.00        | 31.00      | 100.0%   | inf           | 0.00                | 31.00   | 31.00    |

## Closed Trades
| strategy_code | strategy_name | side | entry_time          | exit_time           | entry_price | exit_price | pnl_points | entry_value_time    | entry_mxf_signal | entry_trend | mxf_available | signal_align | trend_align |
| ------------- | ------------- | ---- | ------------------- | ------------------- | ----------- | ---------- | ---------- | ------------------- | ---------------- | ----------- | ------------- | ------------ | ----------- |
| CFCCPm        | 財神列車6號        | bear | 2026-06-25 11:40:04 | 2026-06-25 20:30:31 | 46473       | 46554      | -81        |                     |                  |             | False         | False        | False       |
| CFCTX22m      | 財神列車22號       | bull | 2026-06-25 11:51:28 | 2026-06-25 21:40:46 | 46453       | 46527      | 74         |                     |                  |             | False         | False        | False       |
| CFCPW3m       | 新財神列車3號       | bull | 2026-06-25 04:30:05 | 2026-06-25 22:00:33 | 46574       | 45378      | -1196      |                     |                  |             | False         | False        | False       |
| CFCWIN01m     | 智能引擎1號        | bear | 2026-06-25 16:07:30 | 2026-06-25 22:02:28 | 46636       | 45437      | 1199       |                     |                  |             | False         | False        | False       |
| CFCTX16m      | 財神列車16號       | bear | 2026-06-25 22:07:31 | 2026-06-26 00:15:05 | 45781       | 46123      | -342       |                     |                  |             | False         | False        | False       |
| CFCTX16m      | 財神列車16號       | bear | 2026-06-26 11:22:31 | 2026-06-26 11:58:57 | 44735       | 45033      | -298       |                     |                  |             | False         | False        | False       |
| CFCTX23m      | 財神列車23號       | bull | 2026-06-26 11:44:48 | 2026-06-26 16:51:51 | 44788       | 44461      | -327       |                     |                  |             | False         | False        | False       |
| CFCTX23m      | 財神列車23號       | bull | 2026-06-26 17:35:09 | 2026-06-27 01:56:17 | 44486       | 45202      | 716        |                     |                  |             | False         | False        | False       |
| CFCCPm        | 財神列車6號        | bull | 2026-06-26 23:29:56 | 2026-06-27 03:06:53 | 44839       | 44870      | 31         | 2026-06-26 23:29:00 | none             | gold        | True          | False        | True        |

## By Strategy
| strategy_code | strategy_name | trades | total_points | total_twd_x10 | avg_points | win_rate | profit_factor | max_drawdown_points | best     | worst    |
| ------------- | ------------- | ------ | ------------ | ------------- | ---------- | -------- | ------------- | ------------------- | -------- | -------- |
| CFCCPm        | 財神列車6號        | 2      | -50.00       | -500.00       | -25.00     | 50.0%    | 0.38          | -81.00              | 31.00    | -81.00   |
| CFCPW3m       | 新財神列車3號       | 1      | -1196.00     | -11960.00     | -1196.00   | 0.0%     | 0.00          | -1196.00            | -1196.00 | -1196.00 |
| CFCTX16m      | 財神列車16號       | 2      | -640.00      | -6400.00      | -320.00    | 0.0%     | 0.00          | -640.00             | -298.00  | -342.00  |
| CFCTX22m      | 財神列車22號       | 1      | 74.00        | 740.00        | 74.00      | 100.0%   | inf           | 0.00                | 74.00    | 74.00    |
| CFCTX23m      | 財神列車23號       | 2      | 389.00       | 3890.00       | 194.50     | 50.0%    | 2.19          | -327.00             | 716.00   | -327.00  |
| CFCWIN01m     | 智能引擎1號        | 1      | 1199.00      | 11990.00      | 1199.00    | 100.0%   | inf           | 0.00                | 1199.00  | 1199.00  |

## Open / Unmatched
Open positions:
| strategy_code | strategy_name | side | entry_time          | entry_price | entry_value_time    | entry_mxf_signal | entry_trend |
| ------------- | ------------- | ---- | ------------------- | ----------- | ------------------- | ---------------- | ----------- |
| CFCCPm        | 財神列車6號        | bear | 2026-06-27 03:06:53 | 44870       | 2026-06-27 03:06:00 | bull             | death       |
| CFCTX16m      | 財神列車16號       | bull | 2026-06-29 09:23:49 | 45336       |                     |                  |             |

Unmatched timed exits:
| strategy_code | strategy_name | side | event_time          | event_price | value_time | mxf_signal | trend |
| ------------- | ------------- | ---- | ------------------- | ----------- | ---------- | ---------- | ----- |
| CFCTX16m      | 財神列車16號       | bear | 2026-06-24 23:00:07 | 45831       |            |            |       |

## Conclusion
- Current evidence is insufficient to say `mxf_value` improves these six-strategy entries/exits because only 1 of 9 closed trades has entry-time `mxf_value` coverage.
- The reconstructed baseline for the 9 closed trades is -224 points, 44.4% win rate, 0.90 profit factor, and -1203 points max drawdown.
- The only closed trade with entry-time `mxf_value` coverage was +31 points and passed `trend_align`, `not_against_signal`, `tx_bvav_sign_align`, and `tbta_sign_align`, but failed `signal_align`; this is not enough sample to justify deployment.
- Before a real comparison, backfill or fix `mxf_value` coverage for 2026-06-25, 2026-06-26 daytime, and 2026-06-29 after 08:48.