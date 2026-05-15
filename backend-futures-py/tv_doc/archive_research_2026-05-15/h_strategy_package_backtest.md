# H Strategy Package Backtest

回測起點：2026-04-30 00:00:00

## 策略

第一帳號加碼策略：

```text
H 訊號正常跟單，基礎 1 口
單口 MDD >= 1750 點後，下一筆開始 2 口
MDD 歸零後回到 1 口
MDD 永遠用單口點數計算，帳戶損益才乘上口數
```

第二帳號反向護欄策略：

```text
H 浮虧 >= 150 點，且 1/3/5/10/15 分至少 3 個週期確認 H 方向失效，反向進 1 口
或 H 浮虧 >= 420 點，直接反向進 1 口
第一口獲利 >= 220 點，且 1/3/5 分至少 2 個週期支持反向方向，加第 2 口
加碼前第一口 -220 點停損
H 換方向時出場
```

## 回測結果

第一帳號：

```json
{
  "trades": 11,
  "account_points": 519.0,
  "account_cash_twd": 5190.0,
  "single_points_for_mdd": 842.0,
  "wins": 4,
  "losses": 7,
  "win_rate": 0.364,
  "best": 3132.0,
  "worst": -646.0,
  "account_mdd_points": 2613.0,
  "account_mdd_cash_twd": 26130.0,
  "single_mdd_points": 2290.0,
  "two_lot_trades": 1
}
```

第二帳號護欄：

```json
{
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
}
```

兩帳號合計：

```json
{
  "total_points": 1838.0,
  "cash_twd": 18380.0,
  "mdd_points": 1833.0,
  "mdd_cash_twd": 18330.0
}
```

## 明細

```json
{
  "first_account_trades": [
    {
      "entry_time": "2026-04-23 22:55:05",
      "exit_time": "2026-05-06 09:52:05",
      "side": "bull",
      "entry_price": 38308.0,
      "exit_price": 41440.0,
      "single_points": 3132.0,
      "quantity": 1,
      "account_points": 3132.0,
      "single_mdd_before": 0.0,
      "single_mdd_after": 0.0
    },
    {
      "entry_time": "2026-05-06 09:52:05",
      "exit_time": "2026-05-06 23:26:05",
      "side": "bear",
      "entry_price": 41440.0,
      "exit_price": 42017.0,
      "single_points": -577.0,
      "quantity": 1,
      "account_points": -577.0,
      "single_mdd_before": 0.0,
      "single_mdd_after": 577.0
    },
    {
      "entry_time": "2026-05-06 23:26:05",
      "exit_time": "2026-05-07 08:46:06",
      "side": "bull",
      "entry_price": 42017.0,
      "exit_price": 42233.0,
      "single_points": 216.0,
      "quantity": 1,
      "account_points": 216.0,
      "single_mdd_before": 577.0,
      "single_mdd_after": 361.0
    },
    {
      "entry_time": "2026-05-07 08:46:06",
      "exit_time": "2026-05-07 08:46:06",
      "side": "bear",
      "entry_price": 42233.0,
      "exit_price": 42203.0,
      "single_points": 30.0,
      "quantity": 1,
      "account_points": 30.0,
      "single_mdd_before": 361.0,
      "single_mdd_after": 331.0
    },
    {
      "entry_time": "2026-05-11 23:03:04",
      "exit_time": "2026-05-12 09:35:06",
      "side": "bull",
      "entry_price": 42203.0,
      "exit_price": 41843.0,
      "single_points": -360.0,
      "quantity": 1,
      "account_points": -360.0,
      "single_mdd_before": 331.0,
      "single_mdd_after": 691.0
    },
    {
      "entry_time": "2026-05-12 09:35:06",
      "exit_time": "2026-05-12 15:01:08",
      "side": "bear",
      "entry_price": 41843.0,
      "exit_price": 41539.0,
      "single_points": 304.0,
      "quantity": 1,
      "account_points": 304.0,
      "single_mdd_before": 691.0,
      "single_mdd_after": 387.0
    },
    {
      "entry_time": "2026-05-12 15:01:08",
      "exit_time": "2026-05-12 22:28:05",
      "side": "bull",
      "entry_price": 41539.0,
      "exit_price": 41209.0,
      "single_points": -330.0,
      "quantity": 1,
      "account_points": -330.0,
      "single_mdd_before": 387.0,
      "single_mdd_after": 717.0
    },
    {
      "entry_time": "2026-05-12 22:28:05",
      "exit_time": "2026-05-12 22:28:05",
      "side": "bear",
      "entry_price": 41209.0,
      "exit_price": 41807.0,
      "single_points": -598.0,
      "quantity": 1,
      "account_points": -598.0,
      "single_mdd_before": 717.0,
      "single_mdd_after": 1315.0
    },
    {
      "entry_time": "2026-05-14 01:22:04",
      "exit_time": "2026-05-14 09:46:05",
      "side": "bull",
      "entry_price": 41807.0,
      "exit_price": 41722.0,
      "single_points": -85.0,
      "quantity": 1,
      "account_points": -85.0,
      "single_mdd_before": 1315.0,
      "single_mdd_after": 1400.0
    },
    {
      "entry_time": "2026-05-14 09:46:05",
      "exit_time": "2026-05-14 22:38:04",
      "side": "bear",
      "entry_price": 41722.0,
      "exit_price": 42289.0,
      "single_points": -567.0,
      "quantity": 1,
      "account_points": -567.0,
      "single_mdd_before": 1400.0,
      "single_mdd_after": 1967.0
    },
    {
      "entry_time": "2026-05-14 22:38:04",
      "exit_time": "2026-05-15 09:33:02",
      "side": "bull",
      "entry_price": 42289.0,
      "exit_price": 41966.0,
      "single_points": -323.0,
      "quantity": 2,
      "account_points": -646.0,
      "single_mdd_before": 1967.0,
      "single_mdd_after": 2290.0
    }
  ],
  "guard_trades": [
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
  ],
  "combined_events": [
    {
      "time": "2026-05-06 09:52:05",
      "points": 3132.0
    },
    {
      "time": "2026-05-06 23:26:00",
      "points": 539.0
    },
    {
      "time": "2026-05-06 23:26:05",
      "points": -577.0
    },
    {
      "time": "2026-05-07 08:46:06",
      "points": 216.0
    },
    {
      "time": "2026-05-07 08:46:06",
      "points": 30.0
    },
    {
      "time": "2026-05-12 09:35:06",
      "points": -360.0
    },
    {
      "time": "2026-05-12 15:01:08",
      "points": 304.0
    },
    {
      "time": "2026-05-12 22:28:00",
      "points": 191.0
    },
    {
      "time": "2026-05-12 22:28:05",
      "points": -330.0
    },
    {
      "time": "2026-05-12 22:28:05",
      "points": -598.0
    },
    {
      "time": "2026-05-14 09:46:05",
      "points": -85.0
    },
    {
      "time": "2026-05-14 22:38:00",
      "points": 589.0
    },
    {
      "time": "2026-05-14 22:38:04",
      "points": -567.0
    },
    {
      "time": "2026-05-15 09:33:02",
      "points": -646.0
    }
  ]
}
```

## 判斷

這組規則在目前 4/30 之後的資料為正期望，且第二帳號護欄呈現賺大賠小。
但護欄觸發樣本仍少，不能視為長期保證；正式上線前應持續用同一腳本更新樣本。
