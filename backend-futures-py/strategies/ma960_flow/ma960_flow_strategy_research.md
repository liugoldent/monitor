# MA960 Flow Strategy Research

## Idea

Use the relationship between price and `MA_960` together with MXF flow:

- `tx_bvav > 0` and `mtx_bvav > 0`: big money is long.
- `mtx_tbta < 0`: retail is against big money, classified as `super_long`.
- `mtx_tbta > 0`: retail and big money are both long, classified as `shakeout_long`.
- Entry is only considered when price is above but close to MA960, and MA960 is rising.

This is research only. It is not wired into `webhook_server.py`.

## Data

- Window: 2026-04-29 13:40:00 to 2026-05-21 23:56:00
- Joined 1m + MXF rows: 15,253

## Top Optimizer Results

| Rank | Params | Trades | Points | Win Rate | Max Loss | MDD |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | dist<= 120, slope15> 0, tp=160, sl=60, hold=60m, gold=False | 49 | 914.0 | 38.8% | -147.0 | 367.0 |
| 2 | dist<= 120, slope15> 0, tp=120, sl=60, hold=60m, gold=False | 53 | 893.0 | 41.5% | -151.0 | 359.0 |
| 3 | dist<= 120, slope15> 0, tp=80, sl=80, hold=60m, gold=False | 51 | 839.0 | 54.9% | -151.0 | 440.0 |
| 4 | dist<= 120, slope15> 0, tp=120, sl=60, hold=120m, gold=False | 51 | 834.0 | 39.2% | -151.0 | 359.0 |
| 5 | dist<= 120, slope15> 0, tp=120, sl=80, hold=60m, gold=False | 44 | 833.0 | 45.5% | -151.0 | 401.0 |
| 6 | dist<= 120, slope15> 0, tp=80, sl=80, hold=120m, gold=False | 48 | 805.0 | 54.2% | -151.0 | 440.0 |
| 7 | dist<= 60, slope15> 0, tp=120, sl=60, hold=30m, gold=False | 34 | 748.0 | 50.0% | -79.0 | 365.0 |
| 8 | dist<= 60, slope15> 0, tp=120, sl=80, hold=30m, gold=False | 34 | 748.0 | 50.0% | -79.0 | 365.0 |
| 9 | dist<= 60, slope15> 0, tp=120, sl=120, hold=30m, gold=False | 34 | 748.0 | 50.0% | -79.0 | 365.0 |
| 10 | dist<= 60, slope15> 0, tp=80, sl=60, hold=30m, gold=False | 38 | 742.0 | 52.6% | -79.0 | 365.0 |
| 11 | dist<= 60, slope15> 0, tp=80, sl=80, hold=30m, gold=False | 38 | 742.0 | 52.6% | -79.0 | 365.0 |
| 12 | dist<= 60, slope15> 0, tp=80, sl=120, hold=30m, gold=False | 38 | 742.0 | 52.6% | -79.0 | 365.0 |

## Practical Draft

Chosen params: `dist<= 60, slope15> 0, tp=120, sl=60, hold=30m, gold=False`

Summary: trades=34, points=748.0, win_rate=50.0%, max_loss=-79.0, mdd=365.0.

| Setup | Trades | Points | Win Rate | Avg | Max Loss | MDD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| super_long | 12 | 223.0 | 50.0% | 18.6 | -79.0 | 289.0 |
| shakeout_long | 22 | 525.0 | 50.0% | 23.9 | -76.0 | 84.0 |

## Practical Trades

| Setup | Entry | Exit | Entry | Exit | Points | Dist960 | Slope15 | Reason |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| super_long | 04-30 15:09 | 04-30 15:39 | 39474 | 39540 | 63 | 12 | 1.2 | max hold |
| super_long | 04-30 15:42 | 04-30 16:06 | 39522 | 39646 | 121 | 59 | 0.5 | take profit |
| super_long | 04-30 21:51 | 04-30 21:57 | 39634 | 39558 | -79 | 59 | 5.5 | stop loss |
| super_long | 04-30 22:07 | 04-30 22:08 | 39612 | 39545 | -70 | 32 | 4.4 | stop loss |
| super_long | 04-30 22:24 | 04-30 22:39 | 39621 | 39553 | -71 | 38 | 3.1 | stop loss |
| shakeout_long | 05-04 23:17 | 05-04 23:19 | 40830 | 40757 | -76 | 55 | 13.2 | stop loss |
| super_long | 05-05 09:27 | 05-05 09:29 | 40933 | 40867 | -69 | 35 | 0.5 | stop loss |
| shakeout_long | 05-06 12:58 | 05-06 13:18 | 41423 | 41543 | 117 | 54 | 4.8 | take profit |
| shakeout_long | 05-06 15:14 | 05-06 15:44 | 41443 | 41496 | 50 | 51 | 6.0 | max hold |
| shakeout_long | 05-07 20:27 | 05-07 20:57 | 42297 | 42283 | -17 | 58 | 4.8 | max hold |
| shakeout_long | 05-12 11:00 | 05-12 11:02 | 42081 | 42037 | -47 | 26 | 1.8 | close below ma960 |
| shakeout_long | 05-12 11:07 | 05-12 11:37 | 42085 | 42117 | 29 | 29 | 2.2 | max hold |
| shakeout_long | 05-12 11:46 | 05-12 12:05 | 42094 | 42218 | 121 | 31 | 2.8 | take profit |
| shakeout_long | 05-12 12:12 | 05-12 12:42 | 42130 | 42230 | 97 | 59 | 5.1 | max hold |
| shakeout_long | 05-12 12:52 | 05-12 12:56 | 42122 | 42086 | -39 | 36 | 5.6 | close below ma960 |
| shakeout_long | 05-12 13:04 | 05-12 13:09 | 42116 | 42088 | -31 | 27 | 4.5 | close below ma960 |
| shakeout_long | 05-12 13:10 | 05-12 13:11 | 42092 | 42081 | -14 | 1 | 4.4 | close below ma960 |
| super_long | 05-18 12:27 | 05-18 12:41 | 40771 | 40905 | 131 | 49 | 3.4 | take profit |
| super_long | 05-18 13:31 | 05-18 15:00 | 40789 | 40946 | 154 | 56 | 2.2 | take profit |
| super_long | 05-20 09:23 | 05-20 09:26 | 40377 | 40303 | -77 | 50 | 1.5 | stop loss |
| super_long | 05-20 09:54 | 05-20 10:23 | 40385 | 40507 | 119 | 59 | 0.6 | take profit |
| super_long | 05-20 10:29 | 05-20 10:30 | 40380 | 40313 | -70 | 49 | 2.9 | stop loss |
| super_long | 05-20 15:10 | 05-20 15:40 | 40331 | 40405 | 71 | 52 | 0.3 | max hold |
| shakeout_long | 05-21 18:30 | 05-21 19:00 | 41292 | 41316 | 21 | 57 | 13.2 | max hold |
| shakeout_long | 05-21 19:02 | 05-21 19:32 | 41307 | 41396 | 86 | 51 | 9.6 | max hold |
| shakeout_long | 05-21 19:55 | 05-21 20:25 | 41342 | 41320 | -25 | 57 | 7.5 | max hold |
| shakeout_long | 05-21 20:26 | 05-21 20:56 | 41302 | 41328 | 23 | 4 | 5.4 | max hold |
| shakeout_long | 05-21 20:58 | 05-21 21:24 | 41324 | 41320 | -7 | 13 | 5.8 | close below ma960 |
| shakeout_long | 05-21 21:26 | 05-21 21:27 | 41329 | 41322 | -10 | 7 | 6.6 | close below ma960 |
| shakeout_long | 05-21 21:28 | 05-21 21:31 | 41342 | 41313 | -32 | 19 | 6.9 | close below ma960 |
| shakeout_long | 05-21 21:32 | 05-21 21:51 | 41343 | 41466 | 120 | 18 | 7.1 | take profit |
| shakeout_long | 05-21 22:20 | 05-21 22:38 | 41372 | 41502 | 127 | 17 | 9.6 | take profit |
| shakeout_long | 05-21 22:48 | 05-21 23:08 | 41409 | 41356 | -56 | 39 | 8.2 | close below ma960 |
| shakeout_long | 05-21 23:09 | 05-21 23:39 | 41387 | 41478 | 88 | 7 | 7.2 | max hold |
