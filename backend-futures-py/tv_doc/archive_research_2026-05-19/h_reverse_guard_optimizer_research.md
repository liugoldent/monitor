# H Reverse Guard Optimizer Research

## Goal

Build a first-account guard that only appears after H is already losing, using
`mxf_value.csv` plus 1/5/10/15 minute CSV context. Existing live strategies were
not modified.

## Baseline H Window

- Window: 2026-04-29 13:41:00 to 2026-05-19 22:34:00
- H intervals tested: 15
- H points: 3,336.0
- H max single loss: -598.0 points
- H max drawdown: 2,290.0 points

## Parameter Meaning

- `loss>=`: H unrealized loss required before guard can enter.
- `avg>=`: absolute `mtx_bvav_avg` pressure against H side.
- `stop`: hedge exits when avg reverts past this value. For short hedge, avg >= stop; for long hedge, avg <= -stop.
- `tf>=`: count of invalid 1/5/10/15 minute contexts required.
- `mxf_signal`: whether `signal/trend` must also match the reverse direction.

## Top Results

| Rank | Params | Guard Trades | Combined Points | Combined Max Loss | Combined MDD | Guard Points |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | loss>=50, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=30m | 4 | 3,881.0 | -598.0 | 1,936.0 | 545.0 |
| 2 | loss>=50, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=60m | 4 | 3,881.0 | -598.0 | 1,936.0 | 545.0 |
| 3 | loss>=0, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=30m | 5 | 3,664.0 | -598.0 | 1,936.0 | 328.0 |
| 4 | loss>=0, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=60m | 5 | 3,664.0 | -598.0 | 1,936.0 | 328.0 |
| 5 | loss>=50, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=0m | 5 | 3,878.0 | -598.0 | 1,939.0 | 542.0 |
| 6 | loss>=50, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=15m | 5 | 3,878.0 | -598.0 | 1,939.0 | 542.0 |
| 7 | loss>=0, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=0m | 6 | 3,661.0 | -598.0 | 1,939.0 | 325.0 |
| 8 | loss>=0, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=15m | 6 | 3,661.0 | -598.0 | 1,939.0 | 325.0 |
| 9 | loss>=0, avg>=800, stop=0, tf>=0, mxf_signal=True, remain>=0m | 4 | 3,923.0 | -598.0 | 1,963.0 | 587.0 |
| 10 | loss>=0, avg>=800, stop=0, tf>=0, mxf_signal=True, remain>=15m | 4 | 3,923.0 | -598.0 | 1,963.0 | 587.0 |
| 11 | loss>=0, avg>=800, stop=0, tf>=0, mxf_signal=True, remain>=30m | 4 | 3,923.0 | -598.0 | 1,963.0 | 587.0 |
| 12 | loss>=0, avg>=800, stop=0, tf>=0, mxf_signal=True, remain>=60m | 4 | 3,923.0 | -598.0 | 1,963.0 | 587.0 |

## Best Trade Details

Best params: `loss>=50, avg>=800, stop=0, tf>=0, mxf_signal=False, remain>=30m`

## Practical Draft Choice

For a live guard, prefer the stricter draft version in
`strategy_h_reverse_guard_draft.py`: H must already be losing,
`abs(mtx_bvav_avg)` >= 1,200 against H, and `signal/trend` must confirm the
reverse direction. This is the high-win-rate choice, not the most aggressive
choice: it triggered 3 hedge trades in the current sample, all 3 hedge trades
were winners, and the hedge added +577 points while reducing responsive H losses
from -577 to -465, -330 to -125, and -277 to -17. It still cannot protect
same-minute reversals where there is no time for any guard to enter.

| H Entry | H Exit | H Side | H Points | Hedge | Hedge Entry | Hedge Exit | Guard Points | Combined | Reason |
| --- | --- | --- | ---: | --- | --- | --- | ---: | ---: | --- |
| 05-06 09:52 | 05-06 23:26 | bear | -577 | bull | 05-06 16:20 | 05-06 22:59 | 321 | -256 | exit: H strategy ended |
| 05-12 09:35 | 05-12 15:01 | bear | 304 | bull | 05-12 12:23 | 05-12 13:22 | -208 | 96 | stop: avg reverted |
| 05-12 15:01 | 05-12 22:28 | bull | -330 | bear | 05-12 19:13 | 05-12 22:28 | 241 | -89 | exit: H strategy ended |
| 05-19 15:01 | 05-19 22:18 | bull | -277 | bear | 05-19 17:28 | 05-19 22:18 | 191 | -86 | exit: H strategy ended |
