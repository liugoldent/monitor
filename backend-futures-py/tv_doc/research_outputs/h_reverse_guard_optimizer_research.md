# H Reverse Guard Optimizer Research

## Goal

Build a first-account guard that only appears after H is already losing, using
`mxf_value.csv` plus 1/5/10/15 minute CSV context. Existing live strategies were
not modified.

## Baseline H Window

- Window: 2026-04-29 13:41:00 to 2026-05-29 19:33:00
- H intervals tested: 22
- H points: 6,030.0
- H max single loss: -599.0 points
- H max drawdown: 2,285.0 points

## Parameter Meaning

- `loss>=`: H unrealized loss required before guard can enter.
- `avg>=`: absolute `mtx_bvav_avg` pressure against H side.
- `stop`: hedge exits when avg reverts past this value. For short hedge, avg >= stop; for long hedge, avg <= -stop.
- `tf>=`: count of invalid 1/5/10/15 minute contexts required.
- `mxf_signal`: whether `signal/trend` must also match the reverse direction.

## Top Results

| Rank | Params | Guard Trades | Combined Points | Combined Max Loss | Combined MDD | Guard Points |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | loss>=100, avg>=1500, stop=0, tf>=2, mxf_signal=False, remain>=0m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 2 | loss>=100, avg>=1500, stop=0, tf>=2, mxf_signal=False, remain>=15m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 3 | loss>=100, avg>=1500, stop=0, tf>=2, mxf_signal=False, remain>=30m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 4 | loss>=100, avg>=1500, stop=0, tf>=2, mxf_signal=True, remain>=0m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 5 | loss>=100, avg>=1500, stop=0, tf>=2, mxf_signal=True, remain>=15m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 6 | loss>=100, avg>=1500, stop=0, tf>=2, mxf_signal=True, remain>=30m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 7 | loss>=100, avg>=1500, stop=300, tf>=2, mxf_signal=False, remain>=0m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 8 | loss>=100, avg>=1500, stop=300, tf>=2, mxf_signal=False, remain>=15m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 9 | loss>=100, avg>=1500, stop=300, tf>=2, mxf_signal=False, remain>=30m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 10 | loss>=100, avg>=1500, stop=300, tf>=2, mxf_signal=True, remain>=0m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 11 | loss>=100, avg>=1500, stop=300, tf>=2, mxf_signal=True, remain>=15m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |
| 12 | loss>=100, avg>=1500, stop=300, tf>=2, mxf_signal=True, remain>=30m | 4 | 6,499.0 | -573.0 | 2,098.0 | 469.0 |

## Best Optimizer Params

Best params: `loss>=100, avg>=1500, stop=0, tf>=2, mxf_signal=False, remain>=0m`

## Practical Draft Choice

For a live guard, prefer the stricter draft version in
`strategy_h_reverse_guard_draft.py`: H must be at breakeven or already losing,
`abs(mtx_bvav_avg)` >= 1,200 against H, and `signal/trend` must confirm the
reverse direction. This is the high-win-rate choice, not the most aggressive
choice: it triggered 8 hedge trades in the current
sample, hedge points were 402.0, and combined points
were 6,432.0. Responsive H outcomes changed:
-573 to -461, -318 to -113, -599 to -760, -598 to -184, 149 to -83, -511 to -509, -256 to -224, -44 to -14. It still cannot protect same-minute
reversals where there is no time for any guard to enter.

| H Entry | H Exit | H Side | H Points | Hedge | Hedge Entry | Hedge Exit | Guard Points | Combined | Reason |
| --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | --- |
| 05-06 09:52 | 05-06 23:26 | bear | -573 | bull | 05-06 17:05 | 05-06 22:59 | 112 | -461 | exit: H strategy ended |
| 05-12 15:02 | 05-12 22:28 | bull | -318 | bear | 05-12 19:24 | 05-12 22:28 | 205 | -113 | exit: H strategy ended |
| 05-12 22:28 | 05-14 01:22 | bear | -599 | bull | 05-13 13:24 | 05-13 15:18 | -161 | -760 | stop: avg reverted |
| 05-19 15:02 | 05-19 22:18 | bull | -598 | bear | 05-19 17:09 | 05-19 22:18 | 414 | -184 | exit: H strategy ended |
| 05-20 08:47 | 05-20 15:02 | bull | 149 | bear | 05-20 11:39 | 05-20 15:02 | -232 | -83 | exit: H strategy ended |
| 05-20 15:02 | 05-20 21:47 | bear | -511 | bull | 05-20 17:53 | 05-20 21:39 | 2 | -509 | exit: H strategy ended |
| 05-22 08:47 | 05-22 12:06 | bear | -256 | bull | 05-22 12:04 | 05-22 12:06 | 32 | -224 | exit: H strategy ended |
| 05-28 08:46 | 05-29 10:16 | bear | -44 | bull | 05-29 10:09 | 05-29 10:16 | 30 | -14 | exit: H strategy ended |
