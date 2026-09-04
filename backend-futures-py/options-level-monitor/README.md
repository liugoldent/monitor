# 台指期選擇權支撐壓力監控

每分鐘讀取 Yahoo 股市公開的台指期近月與最近到期臺指選擇權報價，將候選支撐、壓力、選擇權隱含中心與近到期跨式區間印在終端機。

## Windows 啟動

在專案根目錄執行：

```powershell
.\run-options-level-monitor.ps1
```

或雙擊：

```text
run-options-level-monitor.cmd
```

按 `Ctrl+C` 停止。程式預設每60秒抓取一次。

只測試一次：

```powershell
.\run-options-level-monitor.ps1 -Once
```

自訂間隔：

```powershell
.\run-options-level-monitor.ps1 -IntervalSeconds 60
```

## 計算方式

- 選擇權隱含中心：ATM附近各履約價的 `K + Call mid - Put mid`，依距離與買賣價差加權取中位數。
- 台指期等價價位：`選擇權履約價 +（台指期現價 - 選擇權隱含中心）`，避免把指數履約價直接誤當成期貨價。
- 到期波動參考：最接近隱含中心的 Call mid＋Put mid。這是近到期跨式成本，不是1分鐘預測區間。
- 支撐：現價下方 Put OI、成交量增量、距離與整百履約價加權。
- 壓力：現價上方 Call OI、成交量增量、距離與整百履約價加權。
- 分鐘方向：第二輪開始，綜合台指期變化與ATM附近 Call/Put 中間價相對變化。

第一輪沒有前一分鐘快照，因此方向顯示「資料累積中」。第二輪後的「本分鐘量」是兩次累積成交量之差。

## 紀錄

預設將現價上下1,500點內的快照追加至：

```text
runtime/options_level_snapshots.jsonl
```

`runtime/` 已被專案 `.gitignore` 排除，可供後續回測，不會提交到 Git。

## 限制

- Yahoo 頁面不是保證不變的正式行情API；頁面結構改版時解析器可能需要更新。
- 未平倉量不是分鐘級法人分類資料，只用作每日背景權重。
- Call OI大不保證形成壓力，Put OI大也不保證形成支撐；輸出的是候選反應區。
- 正式下單前應以保存的快照回測，並改接券商即時行情來源。
