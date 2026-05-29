# H Profit Retrace Guard Research

## Goal

Avoid the case where H once has a large unrealized profit, then gives too much
back before the official H exit. This is a reverse guard, not a replacement for
H: it waits for H profit first, then enters only after a large retrace with
market-flow pressure against H.

## Baseline Window

- 1m price window: 2026-04-29 13:41:00 to 2026-05-29 19:37:01
- H intervals tested: 22
- H points: 6,030.0
- H cash: 60,300.0
- H max single loss: -599.0 points
- H max drawdown: 2,285.0 points

## Rule

For each active H trade:

1. Track H unrealized close-to-close profit.
2. If the best open profit reaches `profit>=`.
3. If current profit gives back at least `giveback>=` points and the specified ratio of best open profit.
4. If `mtx_bvav_avg` and optionally `signal/trend` oppose H.
5. Open one reverse guard contract.
6. Exit the guard when H exits, or when H recovers to a new favorable close beyond the best H close seen at guard entry.

Round-trip cost: 3 points per guard trade.

## Top Optimizer Results

| Rank | Params | Guard Trades | Combined Points | Combined Max Loss | Combined MDD | Guard Points |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=0, guard_stop=300, remain>=0m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 2 | profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=0, guard_stop=300, remain>=5m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 3 | profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=0, guard_stop=400, remain>=0m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 4 | profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=0, guard_stop=400, remain>=5m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 5 | profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=200, guard_stop=300, remain>=0m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 6 | profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=200, guard_stop=300, remain>=5m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 7 | profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=200, guard_stop=400, remain>=0m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 8 | profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=200, guard_stop=400, remain>=5m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 9 | profit>=750, giveback>=400/50%, avg>=500, signal=yes, recover_stop=0, guard_stop=300, remain>=0m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 10 | profit>=750, giveback>=400/50%, avg>=500, signal=yes, recover_stop=0, guard_stop=300, remain>=5m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 11 | profit>=750, giveback>=400/50%, avg>=500, signal=yes, recover_stop=0, guard_stop=400, remain>=0m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |
| 12 | profit>=750, giveback>=400/50%, avg>=500, signal=yes, recover_stop=0, guard_stop=400, remain>=5m | 1 | 6,737.0 | -599.0 | 2,285.0 | 707.0 |

## Best Optimizer Params

`profit>=750, giveback>=400/50%, avg>=500, signal=no, recover_stop=0, guard_stop=300, remain>=0m`

## Practical Draft Choice

`profit>=750, giveback>=500/50%, avg>=500, signal=yes, recover_stop=0, guard_stop=300, remain>=5m`

- Guard trades: 1
- Guard points: 707.0
- Combined points: 6,737.0
- Combined max single loss: -599.0 points
- Combined MDD: 2,285.0 points

| H Entry | H Exit | H Side | H Points | Max Open Profit | Giveback At Entry | Guard | Guard Entry | Guard Exit | Guard Points | Combined | Reason |
| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | ---: | --- |
| 05-28 08:46 | 05-29 10:16 | bear | -44 | 1,669 | 994 | bull | 05-28 21:02 | 05-29 10:16 | 707 | 663 | exit: H strategy ended |
