# H 策略同帳號口數管理流程

本文件記錄 `auto_trade.py` 目前使用的同帳號口數邏輯。原本概念上的 A 帳號與 B 帳號，實作時都在同一個交易帳號內完成；程式只會計算一個最終目標口數。

```text
實際進場口數 = A_core_qty + B_overlay_qty
```

## 單口 MDD 計算

口數判斷一律使用單口損益計算，不使用實際下單口數放大後的損益。

```text
single_equity = 單口累積損益
single_peak = 單口累積損益高水位
single_mdd = single_peak - single_equity
```

`h_trade.csv` 內的 `pnl` 是微型台指每點 10 元換算後的單口損益。因此：

```text
1000 點 = 10000 元 pnl
2000 點 = 20000 元 pnl
3000 點 = 30000 元 pnl
```

目前 `h_position_size_state.json` 內若有 `initial_drawdown_points`，代表這輪口數計算從指定的既有回撤點數開始，而不是從完整歷史資料的 0 點開始。

## A 核心部位

A 是常駐核心策略，永遠會有部位。

```text
MDD < 1000 點：A_core_qty = 1
MDD >= 1000 點：A_core_qty = 2
MDD >= 2000 點：A_core_qty = 3
MDD 歸 0：A_core_qty 回到 1
```

A 最高 3 口。

## B 加碼部位

B 是回撤修復段加碼，平常不跑。

啟動條件：

```text
策略連續虧損 2 次
且 single_mdd > 0
```

停止條件：

```text
single_mdd 歸 0
```

B 啟動後的口數：

```text
MDD < 2000 點：B_overlay_qty = 1
MDD >= 2000 點：B_overlay_qty = 2
MDD >= 3000 點：B_overlay_qty = 3
```

B 最高 3 口。

## 同帳號總口數

同帳號只下總口數，不會真的分成兩個帳戶下單。

```text
target_qty = A_core_qty + B_overlay_qty
```

口數表：

| 單口 MDD | B 未啟動 | B 已啟動 |
|---:|---:|---:|
| 0 ~ 999 點 | A 1 + B 0 = 1 口 | A 1 + B 1 = 2 口 |
| 1000 ~ 1999 點 | A 2 + B 0 = 2 口 | A 2 + B 1 = 3 口 |
| 2000 ~ 2999 點 | A 3 + B 0 = 3 口 | A 3 + B 2 = 5 口 |
| 3000 點以上 | A 3 + B 0 = 3 口 | A 3 + B 3 = 6 口 |

## 實際下單邏輯

原策略只決定方向，口數管理只決定目標口數。

```text
target_position = signal_direction * target_qty
order_qty = target_position - current_position
```

範例一：反手並加碼

```text
目前持倉：空 3 口
新訊號：做多
target_qty = 5

實際動作：買進 8 口
= 平空 3 口 + 建多 5 口
```

範例二：MDD 歸 0 後降口數

```text
目前持倉：多 5 口
MDD 歸 0
target_qty = 1

實際動作：賣出 4 口
= 多單降到 1 口
```

## 狀態紀錄

狀態紀錄在：

```text
backend-futures-py/tv_doc/h_position_size_state.json
```

主要欄位：

```text
current_drawdown_points      目前單口 MDD 點數
current_drawdown_pnl         目前單口 MDD 金額
consecutive_loss_count       目前連續虧損次數
b_overlay_active             B 是否啟動
a_core_quantity              A 目前口數
b_overlay_quantity           B 目前口數
target_entry_quantity        同帳號下一筆目標進場口數
```

`add_position_active` 是舊欄位，目前保留給人工檢查使用，語意同步為 `b_overlay_active`。

## State JSON 欄位說明

`h_position_size_state.json` 是同帳號口數管理的狀態檔。程式每次計算口數或同步虛擬部位時，會更新這個檔案。

| Key | 說明 |
|---|---|
| `trade_log_start_row` | 從 `h_trade.csv` 第幾筆歷史交易後開始計算本輪口數狀態。用途是保留舊交易紀錄，但讓新帳戶或新資金只從指定位置開始算。 |
| `initial_drawdown_points` | 本輪起算時已經存在的單口 MDD 點數。程式會乘上 `POINT_VALUE` 轉成 pnl，放進單口 equity 起點。 |
| `current_drawdown_points` | 目前單口 MDD 點數，由已寫入 `h_trade.csv` 的 `exiting` pnl 重算。 |
| `current_drawdown_pnl` | 目前單口 MDD 金額。微台每點 10 元，所以 `current_drawdown_pnl = current_drawdown_points * 10`。 |
| `current_drawdown_calculated_at` | 最近一次重算 `current_drawdown_points` / `current_drawdown_pnl` 的時間。 |
| `consecutive_loss_count` | 從最近一筆 `exiting` 往前數，連續 pnl < 0 的筆數。B overlay 用連輸 2 次作為啟動條件。 |
| `add_position_active` | 舊欄位。現在保留給人工檢查與相容舊 state 使用，語意同步為 `b_overlay_active`。 |
| `b_overlay_active` | B overlay 是否已啟動。啟動後會維持到單口 MDD 歸 0，不會因為中途一筆獲利就關掉。 |
| `b_overlay_entry_rule` | 文字說明欄位，記錄 B overlay 的啟動規則。 |
| `b_overlay_exit_rule` | 文字說明欄位，記錄 B overlay 的停止規則。 |
| `a_core_quantity` | A 核心部位目前應該使用的口數。A 永遠存在，依 MDD 1000/2000 點提高到 2/3 口。 |
| `b_overlay_quantity` | B overlay 目前應該使用的口數。B 未啟動為 0；啟動後依 MDD 2000/3000 點提高到 2/3 口。 |
| `target_entry_quantity` | 下一筆同帳號目標進場總口數，等於 `a_core_quantity + b_overlay_quantity`。 |
| `position_size_rule` | 文字說明欄位，記錄目前 A+B 口數規則。 |
| `virtual_position_side` | H1 外部策略目前記錄的虛擬方向，值通常是 `bull` 或 `bear`。用來在實際帳戶沒有倉位時，仍可追蹤策略反手並補寫單口 `exiting`。 |
| `virtual_position_entry_price` | 虛擬部位的進場價格，用來計算下一次虛擬反手時的單口 pnl。 |
| `virtual_position_since` | 虛擬部位建立或更新的時間。 |
| `updated_at` | state JSON 最近一次被寫入的時間。 |
| `note` | 人工備註。目前用來說明這個 state 是從既有 `h_trade.csv` 某一列之後開始計算，舊歷史保留但不納入本輪帳戶口數。 |

### 欄位更新時機

```text
closePosition() 成功平倉
-> 寫入 h_trade.csv 的 exiting
-> 同步 current_drawdown / consecutive_loss_count

auto_trade() 準備進新倉
-> _get_entry_quantity()
-> 讀 h_trade.csv 已落檔 exiting pnl
-> 更新 b_overlay_active、A/B 口數、target_entry_quantity
-> 依 target_entry_quantity 進場
```

### 注意事項

`current_drawdown_points` 與 `current_drawdown_pnl` 只看已經出場並寫入 `h_trade.csv` 的 `exiting` pnl，不看未平倉浮動損益。

`target_entry_quantity` 是下一筆進場口數，不一定等於目前帳戶實際持倉口數。實際持倉仍以券商 API 查詢為準。
