# H Second Account Backtest

回測區間：2026-04-30 00:00:00 -> 2026-05-15 20:53:00
1 分指標資料：2026-04-29 13:40:00 -> 2026-05-15 20:53:00

## 樣本

```text
H 倉位數：12
有 1 分指標資料覆蓋：10
```

## H Loss Guard

第二帳號等 H 看錯、虧損擴大，才反向進場；有獲利後才保守加碼。

```text
觸發交易：3
合計：739.0 點
勝率：2 / 3
最佳：563.0 點
最差：-2.0 點
```

## H Scale Follow

第二帳號在 H 已經獲利後順向跟進，並在第二帳號獲利後加碼。

```text
觸發交易：6
合計：-369.0 點
勝率：2 / 6
最佳：99.0 點
最差：-201.0 點
```

## 明細 JSON

```json
{
  "loss_guard": [
    {
      "h_entry_time": "2026-05-06 09:52:05",
      "h_side": "bear",
      "h_entry_price": 41440.0,
      "h_exit_time": "2026-05-06 23:26:05",
      "h_exit_price": 42017.0,
      "h_points": -577.0,
      "h_is_open": false,
      "second_points": -2.0,
      "exit_reason": "giveback",
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
          "time": "2026-05-06 16:54:00",
          "signal": "add",
          "side": "bull",
          "price": 41843.0,
          "points": 204.0,
          "extra": 2
        },
        {
          "time": "2026-05-06 17:03:00",
          "signal": "exit",
          "side": "bull",
          "price": 41740.0,
          "points": -2.0,
          "extra": "giveback"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 15:01:08",
      "h_side": "bull",
      "h_entry_price": 41539.0,
      "h_exit_time": "2026-05-12 22:28:05",
      "h_exit_price": 41209.0,
      "h_points": -330.0,
      "h_is_open": false,
      "second_points": 178.0,
      "exit_reason": "H exit",
      "events": [
        {
          "time": "2026-05-12 20:00:00",
          "signal": "entry",
          "side": "bear",
          "price": 41351.0,
          "points": -188.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 22:28:00",
          "signal": "exit",
          "side": "bear",
          "price": 41173.0,
          "points": 178.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_entry_price": 41722.0,
      "h_exit_time": "2026-05-14 22:38:04",
      "h_exit_price": 42289.0,
      "h_points": -567.0,
      "h_is_open": false,
      "second_points": 563.0,
      "exit_reason": "H exit",
      "events": [
        {
          "time": "2026-05-14 15:38:00",
          "signal": "entry",
          "side": "bull",
          "price": 41906.0,
          "points": -184.0,
          "extra": 5
        },
        {
          "time": "2026-05-14 22:15:00",
          "signal": "add",
          "side": "bull",
          "price": 42109.0,
          "points": 203.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 22:38:00",
          "signal": "exit",
          "side": "bull",
          "price": 42289.0,
          "points": 563.0,
          "extra": "H exit"
        }
      ]
    }
  ],
  "scale_follow": [
    {
      "h_entry_time": "2026-04-23 22:55:05",
      "h_side": "bull",
      "h_entry_price": 38308.0,
      "h_exit_time": "2026-05-06 09:52:05",
      "h_exit_price": 41440.0,
      "h_points": 3132.0,
      "h_is_open": false,
      "second_points": 99.0,
      "exit_reason": "trailing giveback",
      "events": [
        {
          "time": "2026-04-30 16:00:00",
          "signal": "entry",
          "side": "bull",
          "price": 39580.0,
          "points": 1272.0,
          "extra": 2
        },
        {
          "time": "2026-04-30 18:03:00",
          "signal": "add",
          "side": "bull",
          "price": 39761.0,
          "points": 181.0,
          "extra": 2
        },
        {
          "time": "2026-04-30 21:35:00",
          "signal": "exit",
          "side": "bull",
          "price": 39720.0,
          "points": 99.0,
          "extra": "trailing giveback"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-06 23:26:05",
      "h_side": "bull",
      "h_entry_price": 42017.0,
      "h_exit_time": "2026-05-07 08:46:06",
      "h_exit_price": 42233.0,
      "h_points": 216.0,
      "h_is_open": false,
      "second_points": -32.0,
      "exit_reason": "H exit",
      "events": [
        {
          "time": "2026-05-07 03:21:00",
          "signal": "entry",
          "side": "bull",
          "price": 42197.0,
          "points": 180.0,
          "extra": 2
        },
        {
          "time": "2026-05-07 08:46:00",
          "signal": "exit",
          "side": "bull",
          "price": 42165.0,
          "points": -32.0,
          "extra": "H exit"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-12 09:35:06",
      "h_side": "bear",
      "h_entry_price": 41843.0,
      "h_exit_time": "2026-05-12 15:01:08",
      "h_exit_price": 41539.0,
      "h_points": 304.0,
      "h_is_open": false,
      "second_points": -185.0,
      "exit_reason": "stop or invalid",
      "events": [
        {
          "time": "2026-05-12 09:40:00",
          "signal": "entry",
          "side": "bear",
          "price": 41635.0,
          "points": 208.0,
          "extra": 3
        },
        {
          "time": "2026-05-12 09:45:00",
          "signal": "exit",
          "side": "bear",
          "price": 41820.0,
          "points": -185.0,
          "extra": "stop or invalid"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 01:22:04",
      "h_side": "bull",
      "h_entry_price": 41807.0,
      "h_exit_time": "2026-05-14 09:46:05",
      "h_exit_price": 41722.0,
      "h_points": -85.0,
      "h_is_open": false,
      "second_points": -94.0,
      "exit_reason": "after-add protect",
      "events": [
        {
          "time": "2026-05-14 08:59:00",
          "signal": "entry",
          "side": "bull",
          "price": 41997.0,
          "points": 190.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 09:07:00",
          "signal": "add",
          "side": "bull",
          "price": 42195.0,
          "points": 198.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 09:15:00",
          "signal": "exit",
          "side": "bull",
          "price": 42049.0,
          "points": -94.0,
          "extra": "after-add protect"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-14 09:46:05",
      "h_side": "bear",
      "h_entry_price": 41722.0,
      "h_exit_time": "2026-05-14 22:38:04",
      "h_exit_price": 42289.0,
      "h_points": -567.0,
      "h_is_open": false,
      "second_points": -201.0,
      "exit_reason": "stop or invalid",
      "events": [
        {
          "time": "2026-05-14 13:18:00",
          "signal": "entry",
          "side": "bear",
          "price": 41509.0,
          "points": 213.0,
          "extra": 3
        },
        {
          "time": "2026-05-14 13:29:00",
          "signal": "exit",
          "side": "bear",
          "price": 41710.0,
          "points": -201.0,
          "extra": "stop or invalid"
        }
      ]
    },
    {
      "h_entry_time": "2026-05-15 09:33:02",
      "h_side": "bear",
      "h_entry_price": 41966.0,
      "h_exit_time": "2026-05-15 20:53:00",
      "h_exit_price": 40717.0,
      "h_points": 1249.0,
      "h_is_open": true,
      "second_points": 44.0,
      "exit_reason": "trailing giveback",
      "events": [
        {
          "time": "2026-05-15 09:56:00",
          "signal": "entry",
          "side": "bear",
          "price": 41781.0,
          "points": 185.0,
          "extra": 2
        },
        {
          "time": "2026-05-15 11:20:00",
          "signal": "add",
          "side": "bear",
          "price": 41585.0,
          "points": 196.0,
          "extra": 3
        },
        {
          "time": "2026-05-15 11:40:00",
          "signal": "exit",
          "side": "bear",
          "price": 41661.0,
          "points": 44.0,
          "extra": "trailing giveback"
        }
      ]
    }
  ]
}
```
