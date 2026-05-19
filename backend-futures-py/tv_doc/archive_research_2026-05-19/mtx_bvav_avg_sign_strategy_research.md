# MTX BVAV AVG Sign Strategy Research

## Scope

- Research only; no live strategy files were changed.
- Signal: `mtx_bvav_avg > 0` = long, `mtx_bvav_avg < 0` = short.
- Execution proxy: `webhook_data_1min.csv` close at the same timestamp.
- Test window: 2026-04-29 13:41:00 to 2026-05-19 22:21:00
- Cost model: gross and net are shown; net subtracts 3 points per completed trade.

## Result

| Item | Value |
| --- | ---: |
| MXF rows with price | 3,022 |
| Sign strategy trades | 68 |
| Gross points | 2,855.0 |
| Gross cash | 28,550.0 |
| Net points | 2,651.0 |
| Net cash | 26,510.0 |
| Win rate | 35.29% |
| Avg win / avg loss | 312.88 / -110.41 |
| Strategy max drawdown points | 2,393.0 |

## First Account Same Window

| Item | Value |
| --- | ---: |
| H exits | 15 |
| H total points | 3,336.0 |
| H total cash | 33,360.0 |
| H losing exits | 8 |
| H loss points only | -3,117.0 |
| H loss cash only | -31,170.0 |
| H max drawdown points | 2,290.0 |

## Offset Check

| Item | Value |
| --- | ---: |
| Combined H + sign net points | 5,987.00 |
| Sign net / H loss-only absolute value | 85.05% |

Conclusion: the raw sign strategy can offset some first-account losses only if it is allowed to run as an independent continuous strategy. It is not a clean hedge: the standalone strategy has a large drawdown and frequent flips, so the sign alone is too noisy for production without filters.

## Same-Interval Loss Offset

This check answers the stricter question: during the exact H trades that lost money, did the sign strategy make money in the same time window?

| Item | Value |
| --- | ---: |
| Losing H intervals | 8 |
| H losing-interval points | -3,117.00 |
| Sign strategy points during those intervals | -113.00 |
| Combined losing-interval points | -3,230.00 |
| Same-interval offset ratio | -3.63% |

| H Entry | H Exit | H Side | H Points | Sign Points | Combined |
| --- | --- | --- | ---: | ---: | ---: |
| 2026-05-06 09:52 | 2026-05-06 23:26 | bear | -577 | 197 | -380 |
| 2026-05-11 23:03 | 2026-05-12 09:35 | bull | -360 | -178 | -538 |
| 2026-05-12 15:01 | 2026-05-12 22:28 | bull | -330 | 160 | -170 |
| 2026-05-12 22:28 | 2026-05-12 22:28 | bear | -598 | 0 | -598 |
| 2026-05-14 01:22 | 2026-05-14 09:46 | bull | -85 | 360 | 275 |
| 2026-05-14 09:46 | 2026-05-14 22:38 | bear | -567 | -1,041 | -1,608 |
| 2026-05-14 22:38 | 2026-05-15 09:33 | bull | -323 | 101 | -222 |
| 2026-05-19 15:01 | 2026-05-19 22:18 | bull | -277 | 288 | 11 |

## Largest First-Account Losses

| Time | Side | Price | Points |
| --- | --- | ---: | ---: |
| 2026-05-12 22:28:05 | bear | 41,807 | -598 |
| 2026-05-06 23:26:05 | bear | 42,017 | -577 |
| 2026-05-14 22:38:04 | bear | 42,289 | -567 |
| 2026-05-12 09:35:06 | bull | 41,843 | -360 |
| 2026-05-12 22:28:05 | bull | 41,209 | -330 |
| 2026-05-15 09:33:02 | bull | 41,966 | -323 |
| 2026-05-19 22:18:02 | bull | 39,966 | -277 |
| 2026-05-14 09:46:05 | bull | 41,722 | -85 |

## Worst Sign-Strategy Trades

| Entry | Exit | Side | Entry | Exit | Net Points |
| --- | --- | --- | ---: | ---: | ---: |
| 2026-05-08 09:07:00 | 2026-05-08 10:17:00 | bull | 42,171 | 41,828 | -346 |
| 2026-05-07 09:05:00 | 2026-05-07 09:47:00 | bear | 42,086 | 42,377 | -294 |
| 2026-05-07 18:02:00 | 2026-05-07 21:46:00 | bull | 42,267 | 42,015 | -255 |
| 2026-05-14 21:30:00 | 2026-05-14 21:52:00 | bear | 41,783 | 42,032 | -252 |
| 2026-05-13 15:18:00 | 2026-05-13 16:16:00 | bear | 41,418 | 41,621 | -206 |
| 2026-05-12 09:28:00 | 2026-05-12 11:11:00 | bear | 41,934 | 42,118 | -187 |
| 2026-05-08 17:08:00 | 2026-05-08 17:59:00 | bull | 41,885 | 41,703 | -185 |
| 2026-05-18 18:09:00 | 2026-05-18 19:55:00 | bear | 41,003 | 41,182 | -182 |
