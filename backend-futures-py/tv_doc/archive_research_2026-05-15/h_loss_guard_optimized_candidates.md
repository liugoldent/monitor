# Optimized H Loss Guard Candidates

回測起點：2026-04-30 00:00:00

篩選條件：

```text
交易數 >= 3
總點數 > 0
平均獲利 > 平均虧損絕對值
策略 MDD <= 350 點
單筆最差 >= -220 點
```

## Candidate 1

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 220.0,
    "take_profit": null,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 2

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 220.0,
    "take_profit": 600.0,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 3

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 220.0,
    "take_profit": 800.0,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 4

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 300.0,
    "take_profit": null,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 5

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 300.0,
    "take_profit": 600.0,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 6

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 300.0,
    "take_profit": 800.0,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 7

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 420.0,
    "take_profit": null,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 8

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 420.0,
    "take_profit": 600.0,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 9

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 420.0,
    "invalid_score": 3,
    "stop_loss": 420.0,
    "take_profit": 800.0,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```

## Candidate 10

```json
{
  "summary": {
    "trades": 3,
    "total_points": 1319.0,
    "wins": 3,
    "losses": 0,
    "win_rate": 1.0,
    "avg_win": 439.7,
    "avg_loss": 0,
    "best": 589.0,
    "worst": 191.0,
    "mdd": 0.0,
    "profit_factor": 999,
    "worst_intratrade_mae": -189.0
  },
  "params": {
    "soft_loss": 150.0,
    "hard_loss": 520.0,
    "invalid_score": 3,
    "stop_loss": 220.0,
    "take_profit": null,
    "add_profit": 220.0,
    "add_confirm_score": 2,
    "after_add_protect": null,
    "trail_arm": null,
    "trail_floor": null,
    "exit_on_h_change": true
  },
  "trades": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_points": -577.0,
      "points": 539.0,
      "reason": "H exit",
      "mae": -135.0,
      "mfe": 539.0,
      "events": [
        {
          "time": "2026-05-06 16:50:00",
          "signal": "entry",
          "side": "bull",
          "price": 41639.0,
          "points": -199.0,
          "extra": 4
        },
        {
          "time": "2026-05-06 16:55:00",
          "signal": "add",
          "side": "bull",
          "price": 41860.0,
          "points": 221.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 23:26:00",
          "signal": "exit",
          "side": "bull",
          "price": 42019.0,
          "points": 539.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_points": -330.0,
      "points": 191.0,
      "reason": "H exit",
      "mae": -189.0,
      "mfe": 191.0,
      "events": [
        {
          "time": "2026-05-12 19:54:00",
          "signal": "entry",
          "side": "bear",
          "price": 41364.0,
          "points": -175.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 191.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_points": -567.0,
      "points": 589.0,
      "reason": "H exit",
      "mae": -154.0,
      "mfe": 589.0,
      "events": [
        {
          "time": "2026-05-14 15:35:00",
          "signal": "entry",
          "side": "bull",
          "price": 41880.0,
          "points": -158.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 229.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 589.0,
          "extra": "H exit"
        }
      ]
    }
  ]
}
```
