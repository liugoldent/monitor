# H Bull Loss MTX AVG Short Hedge Research

## Rule

- First account H side must be `bull`.
- H long must already be losing at the MXF timestamp.
- Enter one short hedge when `mtx_bvav_avg < -1000`.
- Stop the short hedge when `mtx_bvav_avg > 0`.
- If the stop does not happen first, exit when the H bull position ends.
- Cost model: hedge net subtracts 3 points per round trip.

## Data

- MXF rows joined with 1 minute close: 3,025
- Window: 2026-04-29 13:41:00 to 2026-05-19 22:24:00
- Candidate H bull intervals: 7
- Triggered hedge trades: 3
- Candidate H bull intervals without hedge trigger: 4
- Hedge exits by avg stop: 1
- Hedge exits by H bull end: 2

## Result

| Item | H Bull Candidates | Hedge Net | H + Hedge On Triggered Trades |
| --- | ---: | ---: | ---: |
| Trades | 7 | 3 | 3 |
| Points | 2,281.0 | 172.0 | 89.0 |
| Cash TWD | 22,810.0 | 1,720.0 | 890.0 |
| Wins | 2 | 2 | 1 |
| Losses | 5 | 1 | 2 |
| Win rate | 28.57% | 66.67% | 33.33% |
| Max drawdown points | 1,098.0 | 286.0 | 132.0 |

## Triggered Trades

| H Entry | H Exit | Hedge Entry | Hedge Exit | H Points | Hedge Net | Combined | Exit Reason |
| --- | --- | --- | --- | ---: | ---: | ---: | --- |
| 2026-05-12 15:01 | 2026-05-12 22:28 | 05-12 19:19 | 05-12 22:28 | -330 | 198 | -132 | exit: H bull strategy ended |
| 2026-05-18 08:45 | 2026-05-18 22:36 | 05-18 08:47 | 05-18 09:01 | 524 | -286 | 238 | stop: mtx_bvav_avg > 0 |
| 2026-05-19 15:01 | 2026-05-19 22:18 | 05-19 17:22 | 05-19 22:18 | -277 | 260 | -17 | exit: H bull strategy ended |
