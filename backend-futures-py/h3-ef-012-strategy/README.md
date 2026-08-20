# H3 + E/F 0/1/2

這個服務同時監控浩克3與群益十二策略訊號，計算永豐帳號的TMF目標淨部位。現階段只透過Discord模擬下單，不會連線永豐或送出實單。

## 固定規則

- E、F方向一致且與H反向：目標0口，不反手。
- E、F沒有共識、互相衝突或任一組淨部位為0：只跟H 1口。
- E、F方向一致且與H同向：跟H 2口。

E與F的方向分別由各自六套策略的淨部位正負號決定。程式不會把十二套原始口數直接送到永豐。

## 訊號格式

浩克3：

```text
期權醫生-浩克3
浩克3V3訊號通知
小型台指近一訊號部位為: 空1口
```

群益：

```text
【08.20 15:00:02】
【訊號通知】【群益】
【6008770】
《策略》CFCTX16m《倉位》-1.0 -> 0.0
```

## 執行方式

只需要執行這一個入口檔：

```bash
cd /Users/kt/Desktop/self/monitor/backend-futures-py/h3-ef-012-strategy
../.venv/bin/python monitor_and_trade.py
```

`monitor_and_trade.py`會完成Telegram監測、H與E/F狀態保存、0/1/2目標計算及Discord模擬下單。它是獨立服務，不會匯入舊H交易程式；未來串接永豐實單時，應直接在這個策略目錄內新增獨立下單模組。

每次程式成功啟動時，會先透過共用Discord webhook送出一次`開始自動交易`。Telegram斷線後的自動重連不會重複發送這則啟動通知。

## 倉位紀錄與判斷流程

兩個CSV是策略判斷的唯一倉位來源：

- `records/h3_position_events.csv`：H3方向變化，包含時間、Telegram event ID、進出動作、原部位、新部位及原始訊息。
- `records/ef_position_events.csv`：十二套E/F倉位變化，另外包含群益帳號、策略代碼及狀態是否需要校正。

另外會產生兩份與`tv_doc/h_trade.csv`欄位完全相同的交易分析紀錄：

- `records/h3_trade.csv`：只記錄H3本身的多空進出。
- `records/h3_ef_trade.csv`：記錄永豐H3+EF混合策略最終0/1/2口的進出。

兩份交易紀錄的欄位皆為`timestamp,action,side,price,pnl,quantity`。`side`使用`bull`或`bear`，`action`使用`enter`或`exiting`；平倉損益沿用既有`h_trade.csv`算法，以每口點數乘以10計算，口數另外保存在`quantity`。加碼或減碼時，會在同一時間先結束原本的整段部位，再以新口數開一段，讓1口與2口期間可以分開統計。

價格只會讀取`tv_doc/webhook_data_1min.csv`中「訊號收到時間以前」最新一筆已記錄的一分鐘收盤價，不會拿之後才出現的K棒回填。2026-08-20建立初始空單時並不知道真正進場價，因此初始價格留白，第一次平倉損益也會留白；從下一次有即時價格的進場開始才會正常計算損益。

每次收到Telegram訊號時，程式先把變化追加至對應CSV；準備模擬下單時，再從頭讀取兩個CSV並重建最新H與十二套E/F部位，最後才執行0/1/2判斷。計算結果先寫入`records/combined_position.json`，下單階段會重新讀取這個總和檔的`final_target_position`，不會直接使用記憶體裡的計算結果。

完整流程：

```text
H3 Telegram → h3_position_events.csv ┐
                                      ├→ combined_position.json → Discord模擬下單 → h3_ef_trade.csv
EF Telegram → ef_position_events.csv ┘

H3 Telegram → h3_trade.csv
```

EF明確支援以下六種變化：`0→1`、`0→-1`、`1→0`、`-1→0`、`1→-1`、`-1→1`。最後兩種會記成平掉原方向並直接轉向。

`runtime/h3_ef_012_state.json`只保存Telegram去重ID、最後模擬目標與最後判斷結果，不作為H、E/F或最終下單部位來源。

2026-08-20初始紀錄依目前已知倉位建立為：

- H3：空1口。
- E組：`CFC07m`空1口、`CFCTX17m`空1口，其餘空手。
- F組：全部空手。
- 因E方向為空、F為空手，E/F尚未形成共同共識，所以目前策略目標是跟H空1口。

### 人工更正最終倉位

打開`records/combined_position.json`，只修改：

```json
"manual_target_position": 0
```

可填`-2`、`-1`、`0`、`1`、`2`。程式會讓人工值優先，更新`final_target_position`並以它送Discord模擬單。設回`null`就恢復使用`calculated_target_position`。不要直接修改`final_target_position`，因為程式會依人工覆寫欄位重新校正它。

監控程式每2秒檢查一次總和檔；執行中手動存檔後，不必等待下一筆Telegram訊號。無效JSON或超出範圍的部位會拒絕下單。

## Discord模擬下單

- 使用`DISCORD_MXF_ALERT_WEBHOOK_URL`，也就是目前送出「服務還啟動著」的同一個webhook。
- 原本`backend-futures-py/monitor_mxf.py`每半小時送出的「服務還啟動著」不做任何修改，仍會照常存在。
- 只有策略目標改變時才送模擬單，例如進場、加碼、減碼、平倉或反向切換。
- 模擬單會清楚標示前次模擬部位、最新目標、買賣方向、口數及觸發原因。
- Discord送出成功後才更新前次模擬部位；若webhook失敗，下一筆有效訊號仍會再次嘗試。
- 模擬下單只讀`combined_position.json`的最終倉位，因此可以在該檔案使用人工覆寫。

範例：

```text
🧪【模擬下單｜H3+EF 0/1/2】
動作：加碼，買進微型台指近一（TMF）1口
模擬部位：多1口 → 多2口
備註：只有Discord模擬通知，未查詢永豐部位、未送出任何實單。
```

## 安全設計

- 程式目前只有Discord模擬模式，沒有真實委託入口。
- 沒有H方向或十二套EF初始持倉未齊全時，不下單。
- 硬性限制絕對部位最多2口。
- 同時間連續EF訊號會等待1秒合併後再計算。
- Telegram事件ID會保存去重，且禁止同時啟動兩個監控實例。
- 不會查詢或修改永豐部位，也不會改寫既有`tv_doc/h_trade.csv`；新策略只寫入自己`records/`下的兩份交易紀錄。

## 環境設定

沿用`../.env`的Telegram設定及既有MXF Discord webhook。加入：

```dotenv
# 群益訊號帳號過濾。
H3_EF_012_CAPITAL_ACCOUNT=6008770

# 與「服務還啟動著」共用。
DISCORD_MXF_ALERT_WEBHOOK_URL=https://discord.com/api/webhooks/...

# 預設false；通常等收到下一筆訊號才送模擬單。
H3_EF_012_SIMULATE_ON_START=false

# 同時間多筆群益訊號的批次等待秒數。
H3_EF_012_BATCH_DELAY_SECONDS=1.0
```

## 啟動

先跑測試：

```bash
cd /Users/kt/Desktop/self/monitor/backend-futures-py/h3-ef-012-strategy
../.venv/bin/python -m unittest discover -s tests -v
```

再啟動Telegram監測與Discord模擬下單：

```bash
cd /Users/kt/Desktop/self/monitor/backend-futures-py/h3-ef-012-strategy
../.venv/bin/python monitor_and_trade.py
```

H3與E/F進出事件分別追加到`records/`下的兩個CSV。執行鎖、Telegram session、去重狀態及最後模擬目標寫在`runtime/`，不提交Git。

## 觀察重點

1. 至少觀察一輪H換向與數筆EF事件。
2. 確認Discord顯示的H、E淨部位、F淨部位與人工計算一致。
3. 確認模擬單的買賣差額正確，例如多1轉空1應顯示賣出2口。
4. `runtime/h3_ef_012_state.json`保存的是模擬部位，不代表永豐真實部位。
5. 未來若要改為實單，必須先加入獨立的永豐部位查詢與差額下單流程，再重新驗證；不可直接把模擬部位當成實倉。
