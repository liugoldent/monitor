# 六策略 RSI60 方向過濾策略

這是一套與 `ef-strong-consensus-strategy`、`h3-ef-012-strategy` 並存的獨立
shadow 策略。它讀取十二套 E/F 策略的原始進出事件，在每次「新方向進場」時加入
TradingView 台指期近一 60 分 RSI14 方向過濾。

目前會產生獨立判斷、影子倉位、Discord 通知及影子績效紀錄。開啟
EF_RSI60_ENABLE_ORDERS 時，會使用 API_KEY2／SECRET_KEY2 對應帳號，將實際
TMF 部位調整成 RSI 過濾後十二套策略的完整淨部位。實單在收到新 EF 訊號後立即
使用可用 RSI 判斷並送單；下一分鐘 Open 只供影子績效對帳。啟動時不追趕既有
影子目標，從啟動後下一筆新 EF 訊號才開始對齊實際帳號。

## 規則

- 多單新進場：最後一根已完成且已寫入價格檔的 60 分 K，RSI14 必須 `>= 50`。
- 空單新進場：最後一根已完成且已寫入價格檔的 60 分 K，RSI14 必須 `<= 50`。
- 不符合時整筆跳過，不會等 RSI 後來過線再追單。
- 原策略出場時，無條件關閉該策略已允許的影子部位。
- 原策略反向時，先關閉舊方向，再用當下 RSI 判斷新方向；新方向不合格就維持空手。
- 若目前盤中應有的上一根 60 分 K 尚未完整寫入，一律阻擋新進場，避免價格 webhook
  停更時沿用過時 RSI；出場不受影響。
- 十二套策略各自過濾後再加總，因此影子總倉範圍是空 12 口到多 12 口。
- 訊號時間以本機實際收到的 `received_at` 為準，模擬成交使用其下一分鐘的
  1 分 K Open；例如 `08:47:22` 收到訊號，使用 `08:48` Open。

60 分 K 依台指期 TradingView session 切分：日盤從 `08:45` 起算，夜盤從
`15:00` 起算並跨午夜。RSI 使用 Wilder RMA 算法，且歷史重播會等該根 K 的最後一筆
一分鐘資料實際寫入後才可使用，避免偷看未來。

## 為什麼採用這個版本

研究來源是 `../tv_doc/six_strategy_signal_events.csv`，以同一個 MXF1! 一分鐘
TradingView webhook 重建價格。正確套用 `08:45`／`15:00` session anchor、Wilder
RSI 與實際資料到達時間後，validation 區間結果如下：

| 指標 | 原始六策略 | RSI60 過濾 |
| --- | ---: | ---: |
| 可計算已平倉交易 | 110 | 71 |
| 總點數 | 6,098 | 7,961 |
| 平均每筆 | 55.4 | 112.1 |
| 勝率 | 50.0% | 57.7% |
| Profit Factor | 1.44 | 1.88 |
| 最大回撤 | -5,011 | -2,933 |

- E 組平均每筆由約 14.0 提高至 65.7，PF 約 1.09 提高至 1.40。
- F 組平均每筆由約 94.0 提高至 159.9，PF 約 1.93 提高至 2.82。
- 三個 expanding walk-forward 測試區段的平均損益、PF、最大回撤方向都改善；第一折
  當時的訓練樣本尚未達固定 100 筆選用門檻，因此嚴格計為 2/3 折可選且改善。
- Validation 只有 13 個交易日，日群聚 bootstrap 的 95% 區間仍跨過零；這是值得
  前瞻追蹤的候選條件，不是已證明可直接送實單的優勢。

日線 MA5/10/20 糾結沒有呈現穩定劣勢，因此本策略不加入日線均線糾結條件。

本資料夾的 `backtest.py` 再採更嚴格的「訊號前五分鐘內必須真的已有價格」口徑，
以 2026-08-26 當時檔案實跑：原始版 311 筆、25,334 點、平均 81.46、PF 1.51、
最大回撤 -8,427；RSI60 版 174 筆、26,383 點、平均 151.63、PF 2.20、最大回撤
-2,930。這是全樣本診斷，不能取代後續全新資料的前瞻驗證。

## 輸入與輸出

輸入沿用既有檔案，不建立第二個 Telegram 連線：

- `../tv_doc/six_strategy_signal_events.csv`
- `../tv_doc/webhook_data_1min.csv`

執行後只會在自己的資料夾產生以下忽略檔：

- `records/rsi60_position.json`：十二策略過濾後部位與目前總影子倉。
- `records/rsi60_decisions.csv`：每筆訊號的 RSI、允許／阻擋與理由。
- `records/rsi60_shadow_trade.csv`：總影子倉的進出與點數／台幣損益。
- `runtime/rsi60_state.json`：來源 CSV 讀取進度及重啟狀態。
- `runtime/rsi60.lock`：跨 macOS／Windows 的單實例鎖。

首次啟動會重播既有事件以重建目前影子部位，但不會把歷史決策補寫進
`records/`，也不會假裝用未知的歷史進場價成交；下一筆新訊號起才正式追加 shadow
紀錄。

## 環境設定

沿用 `../.env`：

```dotenv
# 未設定時退回既有 MXF Discord webhook；兩者都未設定則只印在終端。
DISCORD_EF_RSI60_WEBHOOK_URL=

# 研究固定值為 50；除非重新驗證，不建議調整。
EF_RSI60_THRESHOLD=50

EF_RSI60_POLL_SECONDS=2
```

## Windows 執行

合併分支並建立好 `backend-futures-py\.venv` 後，在 PowerShell 執行：

```powershell
cd C:\path\to\monitor\backend-futures-py\ef-rsi60-filter-strategy
..\.venv\Scripts\python.exe -m unittest discover -s tests -v
..\.venv\Scripts\python.exe backtest.py
..\.venv\Scripts\python.exe monitor_and_trade.py
```

## macOS 執行

```bash
cd /Users/kt/Desktop/self/monitor/backend-futures-py/ef-rsi60-filter-strategy
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python backtest.py
../.venv/bin/python monitor_and_trade.py
```

Docker shadow 會優先讀取 `DISCORD_EF_RSIFILTER_WEBHOOK_URL`，並等待訊號
嚴格下一根1分K出現後，使用該根 Open 記錄影子成交。

`backtest.py` 使用訊號當下以前五分鐘內最新、已寫入的一分鐘收盤代理成交價，輸出
原始六策略和 RSI60 過濾版本的已平倉交易比較；不扣手續費、交易稅與滑價。

## 上線前觀察

1. 先保持 shadow mode，至少累積一段全新的獨立交易日。
2. 核對 TradingView 60 分 RSI 與 `rsi60_decisions.csv`，特別是 08:45 日盤及
   15:00 夜盤的第一根 K。
3. 分開檢查多單、空單及 E/F；目前空單改善幅度明顯較弱。
4. 納入交易成本後再決定是否把過濾器接到正式六策略下單入口。
5. 若未來要送實單，必須另做帳戶部位對帳、失敗重試與風險上限；不要直接把本
   shadow monitor 改成呼叫既有下單函式。
