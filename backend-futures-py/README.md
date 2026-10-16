## 啟動環境
```bash
source venv/bin/activate
```

## H3 Discord 單口回撤觀察

`telegram_signal_relay.py` 在可信 H3 bot 的原訊號後附上回撤觀察，送至既有
`DISCORD_H_TRADE_WEBHOOK_URL`。只有觀察通知，不會呼叫券商或改變帳戶口數。

- 固定單口的已平倉淨損益，以點數計算，每筆來回成本估算 4.8 點。
- 回撤百分比＝單口已平倉曲線從前高回撤點數／本次訊號當時的指數估值。
- 第1口從啟動後的新H3換向訊號開始持續跟隨，不等回撤或修復。
- 同一輪先回撤達 2.5%，再修復到 ≤1.25% 的新換向訊號，提醒一次
  「第2口加碼觀察條件成立」。這是候選窗口，無法保證下一筆不虧損。
- 首次進場那一輪維持1口；先回前高，後續新一輪達同樣修復條件才2口，
  回撤歸零恢復1口，總口數上限2口。群益與凱基共用這條單口影子曲線。
- 第2口只在新的修復確認訊號啟用，不憑舊提醒追單；第1口不受該門檻限制。
  每一輪僅首次修復主動提醒。每日重啟保留帳戶1／2口階段與單口曲線，不重置。
- 同方向、重複訊息不重算一筆交易，也不產生新的進場提示。
- 分母與新訊號的影子成交價採接收時已取得的 `MXF1!` 一分鐘收盤價，
  同時檢查接收時間及 K 棒時間（180 秒內），不使用未來價格。
- 缺失、過期行情或歷史不完整時，只顯示「資料待核對」，原訊號繼續轉送。

歷史基準由使用者提供的固定單口 H3 匯出建立；重算配對進出場，忽略 Entry 列
提前帶出的未來獲利與匯出累計欄位。基準保存於已忽略的
`telegram-relay-runtime/h3_recovery_seed_v2.json`，不依賴 Downloads 路徑。
後續只重放 `telegram_signal_events.jsonl` 中可信 bot 的 `received` 事件。
基準與接收紀錄重建觀察狀態，因此重啟後不會失去等待修復狀態。

首次建立（已有基準時會拒絕覆寫）：

```powershell
py -3 backend-futures-py/h3_entry_watch.py --seed C:/Users/USER/Downloads/h3.csv --output backend-futures-py/telegram-relay-runtime/h3_recovery_seed_v2.json
```

新增 helper 的 Compose 唯讀掛載後，用
`docker compose up -d --no-build --no-deps telegram-signal-relay` 套用；
只更新已掛載的程式時可重啟該服務。H3 回撤觀察不使用舊 `h_trade.csv` 的混合口數紀錄，
也不讀取群益或凱基的實際金額回撤。`h3_recovery_sizing.py` 共用這份基準和行情，
各券商在自己的SQLite `h3_recovery_state` 保存啟動、加碼與已確認庫存；成交狀態
和委託guard同一交易提交。既有未確認委託、舊倉或人工變動不自動移轉／平倉。
服務啟動不送單、不補歷史訊號；已啟動的1口階段持續跟隨，不會每次歸零就停跑。
凱基原本實際帳戶回撤3%的加碼已由新規則取代；舊帳本保留供核對。

## MXF／股威 API 總結（2026-10-13）

### 常態服務實際使用：每分鐘 13 次 GET

`monitor_mxf.py` 沿用原有開盤時段：平日 08:45–13:45、15:00–23:59:59，
以及週一至週六 00:00–05:00（Asia/Taipei；沿用既有時段判斷，沒有另外判斷休市日）。
每輪原有快照 1 次，加上以下 12 個 history 商品／指標組合，每次最多並行 4 個 GET。
四類指標的 open/high/low/close/volume 全部印在 `MXF Market Monitor` 終端機。
正數嚴格大於 1500 的欄位會在原指標列標示 `[>1500]`。
依使用者要求，已移除末尾另外排序的「大於 1500 的正數欄位」彙整清單。
正數大小本身不能證明該欄位屬於近月或次月。

| 用途 | GET API | 使用欄位 | 寫入／輸出 |
| --- | --- | --- | --- |
| 原有股威籌碼快照 | [chip960_tradeinfo](https://market-data-api.futures-ai.com/chip960_tradeinfo/) | `tx_bvav` 坦克、`mtx_bvav` 游擊隊合計、`mtx_tbta` 炮灰 | 原有 MongoDB 與 `tv_doc/mxf_value.csv` 流程 |
| 掛單能量 `#trade_info` | [API 範例](https://tvapi.futures-op.com/history?symbol=MTX-2%23trade_info&resolution=1&days=1&ws=true&metaData=true&agent=daddy960&product=web_goodway) | TX-1、TX-2、MTX-1、MTX-2，共 4 次；全部 OHLCV 欄位 | 只列印日誌 |
| 主散方向 `#bs_indicator_acc` | [API 範例](https://tvapi.futures-op.com/history?symbol=MTX-1%23bs_indicator_acc&resolution=1&days=1&ws=true&metaData=true&agent=daddy960&product=web_goodway) | TX-1、TX-2、MTX-1、MTX-2，共 4 次；全部 OHLCV 欄位 | 只列印日誌 |
| 台指籌碼 `#goodway_chip960` | [API 範例](https://tvapi.futures-op.com/history?symbol=TX-2%23goodway_chip960&resolution=1&days=1&ws=true&metaData=true&agent=daddy960&product=web_goodway) | TX-1、TX-2，共 2 次；全部 OHLCV 欄位 | 列印日誌；high（游擊隊）跨越 ±1000／±1500 與開盤通知 |
| 股威力道來源 `#goodway_sfChip960` | [API 範例](https://tvapi.futures-op.com/history?symbol=TX-1%23goodway_sfChip960&resolution=1&days=1&ws=true&metaData=true&agent=daddy960&product=web_goodway) | TX-1、TX-2，共 2 次；全部 OHLCV 欄位 | 只列印日誌 |

上表 history 連結用 `days=1`，方便直接貼到瀏覽器；**服務實際改用最近 15 分鐘的範圍**，
不帶 `days`，參數如下：

```text
GET https://tvapi.futures-op.com/history
symbol=<商品>#<指標後綴>（組合見上表）
resolution=1
from=目前 Unix 秒 - 900
to=目前 Unix 秒
ws=true
metaData=true
agent=daddy960
product=web_goodway
```

URL 中 `#` 必須編碼成 `%23`；Python 的 `requests.get(..., params=...)` 自動處理。
`from/to` 單位是 Unix 秒，`bars[].time` 單位是 Unix 毫秒。
這裡的 `ws=true` 是 HTTP 查詢參數，服務仍是每分鐘 HTTP GET，沒有建立 WebSocket 連線。

其他參數與商品形式的檢查（2026-10-13；未加入常態輪詢）：

- 公開圖表 datafeed 的 history 參數是 symbol、resolution、from、to、ws、metaData，
  共用客戶端另加 agent、product；目前未看到額外的近／次月 month 或 contract_month 參數。
- 股威指標沿用圖表週期，支援 1、2、3、5、15、30、60 分鐘。實測 resolution=1、5、15
  的回應數值與時間粒度不同，但每個週期內 TX-1／TX-2 的該歷史區間仍相同。
- 公開快捷按鈕在 TX-1 與 TXAM-1 間切換；symbols API 的商品描述為 TXAM-1「台指期(日盤)」、
  TXPM-1「台指期(夜盤)」。指標 symbol 也能沿用這些前綴，不只 TX-1／TX-2。
  測試 22:00–22:20：TXPM-1#goodway_chip960 與 TX-1 相同；TXAM-1 回傳日盤資料，
  TXAM-2／TXPM-2 的該指標回傳 noData。symbols 能解析不代表 history 有資料。
  23:20:59 再測 TX-1／TXAM-1：最近 24 小時的 300 根共同日盤資料，open/high/low 全部相同；
  TXAM-1 最新停在 13:44（坦克 -135、游擊隊 -165、炮灰 2307），
  TX-1 最新為 23:20（坦克 330、游擊隊 753、炮灰 -647）。
  查詢 22:08–22:15 時，TXAM-1 仍回傳日盤 300 根，沒有 22:11 資料；
  TX-1 的 22:11 游擊隊為 1484。此比較支持 TXAM-1 為日盤篩選，未支持獨立次月籌碼。
- 共用 API 的另一條 kbars 流程有 intraday、from_DEBUG_、to_DEBUG_；圖表 getBars 流程未帶這些。
  加 intraday=true 或 intraday=false，測試回傳 498 根而基準為 21 根，
  但 22:11 的游擊隊值皆為 1484；未證實 intraday 可切換月份，false 的字串行為也未確認。
- 登入圖表還有 Authorization／WebSocket token 與即時訂閱；尚未取得登入後的實際網路紀錄。
  公開 JS 與無 Token GET 的結果，不能排除登入即時資料的差異。

來源：[圖表 datafeed 公開 JS](https://goodway.960daddy.com/_next/static/chunks/3921-b0b1dbba0134d0fc.js)、
[股威指標公開 JS](https://goodway.960daddy.com/_next/static/chunks/8100-8d377db6f8861993.js)、
[共用 API 公開 JS](https://goodway.960daddy.com/_next/static/chunks/pages/_app-2c14f977ebad3bda.js)。

快照回應格式：

```json
{"tx_bvav": 386, "mtx_bvav": 564, "mtx_tbta": -101}
```

history 回應格式（以下是欄位示意，數值不是即時報價）：

```json
{
  "bars": [
    {"time": 1791898860000, "open": 1733, "high": 0, "low": 0, "close": 0, "volume": 0}
  ],
  "metaData": {"noData": false}
}
```

每列顯示該 API 最新資料時間，不假設 12 組回應同步更新；資料落後超過 120 秒會標示。
單一 API 失敗或無資料時列出原因，其餘成功的資料仍會輸出。
游擊隊組成比對另外取小台兩個商品**最新的共同資料時間**，
以近月 `open` 加次月 `open`，不混用不同分鐘；無共同時間或缺值則列出原因。
最新分鐘的值仍可能更新，快照 API 沒有時間欄位，不能保證它與 history 在同一秒更新。
CSV 欄位、原有訊號與平均值計算沿用原流程。
2026-10-14 加入游擊隊跨越通知：只追蹤 `TX-1#goodway_chip960.high` 與
`TX-2#goodway_chip960.high`，使用 `.env` 的 `DISCORD_MXF_ALERT_WEBHOOK_URL`。
前值 ≤1500、現值 >1500 時通知向上突破；前值 ≥-1500、現值 <-1500 時通知向下跌破。
另加入 ±1000：前值 ≤1000、現值 >1000 時通知向上突破；前值 ≥-1000、現值 <-1000
時通知向下跌破。兩組門檻獨立判斷；一次跳越兩個門檻會各發一則門檻通知。
等於門檻、持續在門檻外，以及返回門檻內都不通知；返回後再次跨出會再通知。
近次月同一輪、同資料時間、同方向及同前後數值的跨越合併一則，訊息列出兩個前綴。
啟動或重啟後以每個前綴最新有效值建立基準，不補發啟動前的跨越。
後續按時間處理最近 15 分鐘視窗內的新資料，也追蹤最新分鐘 high 的修訂；
缺值或 API 失敗保留前值，通知發送失敗會留在記憶體佇列中供下輪重試（重啟會清空）。
沿用每分鐘輪詢與開盤時段，所以通知在下一輪取得跨越資料時發送。
另加入「960下游擊正／960上游擊負數」開盤通知，送至相同 alert webhook：
只看當日平日的 08:45 與 15:00 一分鐘棒，分別在 08:46、15:01 收完後判斷。
同分鐘 `webhook_data_1min.csv` 的 `MXF1!` 收盤價 < `MA_960` 且
`goodway_chip960.high` > 0 時通知「開盤轉多」；收盤價 > `MA_960` 且 high < 0
時通知「開盤轉空」。相等、游擊為 0 或不符合雙條件都不通知；不要求跨越 ±1000／±1500。
價格與籌碼必須對齊開盤棒的原始時間，缺資料會在原有 15 分鐘視窗內等待重試，
不以隔壁分鐘或 API 的最新棒代替。近次月同值合併一則；每個前綴每場開盤只判斷一次。
判斷紀錄及待送訊息保存至 `tv_doc/goodway_opening_alert_state.json`，重啟後仍可去重及重試。
這次保留的是上述四類已確認的 API；沒有把網站所有探查路徑加入每分鐘輪詢。

先前游擊隊組成輸出範例（2026-10-13 的歷史樣本；目前另有完整指標列）：

```text
2026-10-13 21:42:18 股威游擊隊近次月｜資料時間 2026-10-13 21:42:00
  游擊隊：近月 1798 ＋ 次月 -127 ＝ 合計 1671
```

Docker Compose 唯讀掛載 `monitor_mxf.py`。已套用至 `monitor-mxf`；後續程式修改可用
`docker compose up -d --no-build --no-deps monitor-mxf` 套用掛載設定，
已掛載後用 `docker compose restart monitor-mxf` 載入新程式。

### 下午研究得到的指標關係

| 指標 | `goodway_chip960` 欄位 | 同分鐘比對得到的組成 |
| --- | --- | --- |
| 坦克 | `open` | `TX-1#trade_info.open + TX-2#trade_info.open` |
| 游擊隊 | `high` | `MTX-1#trade_info.open + MTX-2#trade_info.open` |
| 炮灰 | `low` | `MTX-1#trade_info.low + MTX-2#trade_info.low` |

前端 `goodway-txChip`（台指籌碼）把 `goodway_chip960` 的 open/high/low
分別映射到 `tx_bvav/mtx_bvav/mtx_tbta`，卡片顯示為坦克／游擊隊／炮灰。
`trade_info.open` 在前端掛單能量指標標為「大戶掛單」，`low` 標為「散戶成交」。
三個合計關係在 117 筆對齊且排除仍更新的分鐘中全部相符；游擊隊另外 100 筆獨立時段
也全部相符，共 217/217。這是數值比對得到的關係，尚無後端正式公式或大戶分類規則。

實際畫面比對（使用者提供 `191938.jpg` 與 `S__27713583.jpg`）：

- 第一張圖是 TX-1、1 分鐘；底部台指籌碼的灰色 -203＝坦克／外資（open），
  紅色 2098＝炮灰／散戶（low），藍色 -30＝游擊隊／作手（high），
  黃色 786.96＝作手／游擊隊 23 期平均（前端計算）。
- 今日 13:30 的 TX-1#goodway_chip960 與 TX-2#goodway_chip960 均回傳
  open=-203、high=-30、low=2098，三項原始值精確對上第一張截圖。
  同分鐘一般價格 TX-1.close=48449、TX-2.close=48643，價格月份不同但籌碼相同。
- 以事後 history 的 23 根 high 重算平均為 696.217391，與截圖黄色 786.96 不同；
  原始三欄吻合不代表前端即時平均也能重建，差異原因尚未確認。
- 第二張圖的左側 TX-1／TXAM-1 是一般與日盤的快捷切換；卡片坦克 159、炮灰 -46、
  游擊隊 706。截圖未顯示日期，也未呈現 TX-2 的實際畫面。
- 圖表的商品切換函式會 setSymbol，台指籌碼使用目前 ticker 加 #goodway_chip960，
  正規式支援 TX-2；因此切 TX-2 時預期會查 TX-2#goodway_chip960。
  尚無登入後實際切換畫面的網路紀錄，不能由程式支援就推定籌碼按月份獨立計算。

因此，次月游擊隊的**組成值**讀 `MTX-2#trade_info.open`。
`TX-2#goodway_chip960` 曾和 `TX-1#goodway_chip960` 回傳同一合計序列，
不能用它直接當作獨立的次月游擊隊。

其他保留的結論：

- `#goodway_sfChip960` 的 open/high/low/close，分別與大台近月大戶掛單、
  大台近月散戶成交、大台次月大戶掛單、大台次月散戶成交相符（58/58 筆）。
- `#bs_indicator_acc` 主散方向：high＝大戶、low＝散戶、open＝總和；近次月數值不同。
- `#trade_info` 掛單能量：open＝大戶掛單、low＝散戶成交、high＝市場掛單；近次月數值不同。
- history 不帶 `ws=true&metaData=true` 時，tvapi OP 常見格式是
  `{t:[],o:[],h:[],l:[],c:[],v:[],s:"ok"}`；其中 t 是 Unix 秒，o/h/l 對應 open/high/low。
- `tvapi.futures-ai.com` 的次月 `goodway_chip960` 曾回傳 `noData`；
  OP、SignalR、F03 的該合計近次月曾相同。以上是當日觀察，不保證其他時間的行為。
- `stkfut_tradeinfo` 的 near_month/next_month 是股票期貨，當日資料沒有 TX／MTX，
  不適合作為台指游擊隊次月來源。
- 可直接開啟 [國內監控頁](https://goodway.960daddy.com/goodway/monitors)
  及 [海外監控頁](https://goodway.960daddy.com/goodway/os-monitors)；頁面 HTML 可讀不代表會員圖表免登入。
  沒有找到可確認的公開測試帳號，也沒有實際錄到登入後切換商品的網路請求。

### 公開 GET 探查清單（精簡留存）

2026-10-13 從公開首頁、pages chunks 與共用模組追查，測試 49 個 host/path、88 組參數。
86 組 HTTP 200（包含空陣列、null 或 noData），2 組 HTTP 500。
不含未公開後端完整清單；未攜帶登入 Token。以下是當日觀察的回應摘要，不代表目前保證可用。
為減少 Changes，下載的 JS、原始 JSON、探查腳本及 HTML 報表已移除，只留此摘要。

<details>
<summary>展開 49 個 GET 路徑、當時參數與回應摘要</summary>

| GET 路徑 | 當時參數 | 回應摘要 |
| --- | --- | --- |
| [api.futures-op.com/api/opcontracts](https://api.futures-op.com/api/opcontracts) | `from=Unix秒; to=Unix秒` | contract_month, settlement_date, default |
| [api.futures-op.com/api/options_greeks](https://api.futures-op.com/api/options_greeks) | `contract_month=202610W2; to=Unix秒` | 陣列：symbol, iv, delta |
| [api.futures-op.com/api/options_price](https://api.futures-op.com/api/options_price) | `contract_month=202610W2; to=Unix秒` | 陣列：symbol, open, high, low, close, volume |
| [api.futures-op.com/api/v2/opbs](https://api.futures-op.com/api/v2/opbs) | `contract_month=202610W2; from=Unix秒; to=Unix秒` | call, put, datetime |
| [api.market-data.futures-ai.com/amplitude_statistic](https://api.market-data.futures-ai.com/amplitude_statistic) | `symbol=TX-1; from=Unix秒; to=Unix秒; bins=10; resolution=60; outlier=1` | HTTP 500：message |
| [api.market-data.futures-ai.com/average_amplitude](https://api.market-data.futures-ai.com/average_amplitude) | `symbol=TX-1` | symbol, interval, data |
| [api.market-data.futures-ai.com/average_volume/](https://api.market-data.futures-ai.com/average_volume/) | `symbol=TX-1` | symbol, interval, data |
| [api.market-data.futures-ai.com/big3_stock](https://api.market-data.futures-ai.com/big3_stock) | `date=2026-10-13; symbols=2330` | 陣列：date, symbol, foreigner_diff_volume, trust_diff_volume, dealer_diff_volume, dealer_hedge_diff_volume, big3_diff_volume, foreigner_diff_value |
| [api.market-data.futures-ai.com/chip960_tradeinfo](https://api.market-data.futures-ai.com/chip960_tradeinfo) | `無` | tx_bvav, mtx_bvav, mtx_tbta |
| [api.market-data.futures-ai.com/diff_statistic](https://api.market-data.futures-ai.com/diff_statistic) | `symbol=TX-1; from=Unix秒; to=Unix秒; bins=10; resolution=60; outlier=1` | HTTP 500：message |
| [api.market-data.futures-ai.com/latest_value](https://api.market-data.futures-ai.com/latest_value) | `key=atm_iv` | value |
| [api.market-data.futures-ai.com/stkfut_tradeinfo](https://api.market-data.futures-ai.com/stkfut_tradeinfo) | `date=2026-10-13` | 依商品／股票代碼索引的物件 |
| [api.market-data.futures-ai.com/stock_futures_weight](https://api.market-data.futures-ai.com/stock_futures_weight) | `limit=3` | 依商品／股票代碼索引的物件 |
| [api.market-data.futures-ai.com/stock_weight/](https://api.market-data.futures-ai.com/stock_weight/) | `exchange=TWSE` | 依商品／股票代碼索引的物件 |
| [api.market-data.futures-ai.com/taifex_options_oi](https://api.market-data.futures-ai.com/taifex_options_oi) | `contract_month=202610W2` | 陣列：month, data |
| [api.market-data.futures-ai.com/unify_table](https://api.market-data.futures-ai.com/unify_table) | `from_dt=ISO日期時間; to_dt=ISO日期時間` | t, date, foreigner_txf, foreigner_mxf, foreigner_tmf, foreigner_opt, dealer_txf, dealer_mxf … |
| [api.market-insight.futures-ai.com/api/chip/unify_table](https://api.market-insight.futures-ai.com/api/chip/unify_table) | `from_dt=ISO日期時間; to_dt=ISO日期時間` | t, date, foreigner_txf, foreigner_mxf, foreigner_tmf, foreigner_opt, dealer_txf, dealer_mxf … |
| [api.market-insight.futures-ai.com/api/fundamental/important_info](https://api.market-insight.futures-ai.com/api/fundamental/important_info) | `symbols=2330; datetime_gte=ISO日期時間; datetime_lte=ISO日期時間` | 空陣列（未確認元素格式） |
| [api.market-insight.futures-ai.com/api/market_info/latest_trade_date](https://api.market-insight.futures-ai.com/api/market_info/latest_trade_date) | `無` | date |
| [api.market-insight.futures-ai.com/api/market_info/latest_trade_dates](https://api.market-insight.futures-ai.com/api/market_info/latest_trade_dates) | `無` | 陣列：date |
| [api.market-insight.futures-ai.com/api/market_info/options_atm](https://api.market-insight.futures-ai.com/api/market_info/options_atm) | `contract_month=202610W2` | atm_strike_price, synthetic_futures_price |
| [api.market-insight.futures-ai.com/api/market_info/options_contracts](https://api.market-insight.futures-ai.com/api/market_info/options_contracts) | `from=Unix秒; to=Unix秒` | default_contract_month, data |
| [api.market-insight.futures-ai.com/api/market_info/options_greeks](https://api.market-insight.futures-ai.com/api/market_info/options_greeks) | `contract_month=202610W2` | 依商品／股票代碼索引的物件 |
| [api.market-insight.futures-ai.com/api/market_info/options_oi](https://api.market-insight.futures-ai.com/api/market_info/options_oi) | `contract_month=202610W2` | 依商品／股票代碼索引的物件 |
| [api.market-insight.futures-ai.com/api/market_info/oversea_futures_info](https://api.market-insight.futures-ai.com/api/market_info/oversea_futures_info) | `無` | 陣列：name, exchange, pricescale, symbol |
| [api.market-insight.futures-ai.com/api/market_info/pivot](https://api.market-insight.futures-ai.com/api/market_info/pivot) | `symbol=TX; resolution=60` | symbol, resolution, direction, pivot, current, type, action |
| [api.market-insight.futures-ai.com/api/market_info/stkfut_tradeinfo](https://api.market-insight.futures-ai.com/api/market_info/stkfut_tradeinfo) | `date=2026-10-13` | 依商品／股票代碼索引的物件 |
| [api.market-insight.futures-ai.com/api/market_info/stock_futures_weight](https://api.market-insight.futures-ai.com/api/market_info/stock_futures_weight) | `無` | 依商品／股票代碼索引的物件 |
| [api.market-insight.futures-ai.com/api/market_info/stock_intraday_trading_flag](https://api.market-insight.futures-ai.com/api/market_info/stock_intraday_trading_flag) | `無` | 陣列：symbol, intraday_trading_flag |
| [api.market-insight.futures-ai.com/api/market_info/us_stock_marketcap](https://api.market-insight.futures-ai.com/api/market_info/us_stock_marketcap) | `無` | 陣列：rank, name, symbol, market_cap, price |
| [api.market-insight.futures-ai.com/api/misc/news](https://api.market-insight.futures-ai.com/api/misc/news) | `query=2330` | generator, title, link, language, webMaster, copyright, lastBuildDate, image … |
| [api.market-insight.futures-ai.com/api/monitor/advance_decline](https://api.market-insight.futures-ai.com/api/monitor/advance_decline) | `date=2026-10-13` | 陣列：avg_change_percent, count, count_total, percentage, name |
| [api.market-insight.futures-ai.com/api/monitor/daily_rank](https://api.market-insight.futures-ai.com/api/monitor/daily_rank) | `date=2026-10-13` | 陣列：symbol, open, high, low, close, volume, amount, last_close |
| [api.market-insight.futures-ai.com/api/monitor/estimated_settlement_price](https://api.market-insight.futures-ai.com/api/monitor/estimated_settlement_price) | `symbol=TX-1; date=2026-10-13` | symbol, estimated_settlement_price, datetime |
| [api.market-insight.futures-ai.com/api/monitor/win168_signal](https://api.market-insight.futures-ai.com/api/monitor/win168_signal) | `無` | entry_time, entry_price, position |
| [api.market-insight.futures-ai.com/api/screener/bole_kd](https://api.market-insight.futures-ai.com/api/screener/bole_kd) | `bs=b; date=2026-10-13` | symbols |
| [api.market-insight.futures-ai.com/api/screener/good178](https://api.market-insight.futures-ai.com/api/screener/good178) | `bs=b; date=2026-10-13` | symbols |
| [api.market-insight.futures-ai.com/api/screener/weng888_stock_branch](https://api.market-insight.futures-ai.com/api/screener/weng888_stock_branch) | `無` | 陣列：symbol, branch_count, total_diff_volume, total_diff_amount, top1_branch_id, top1_branch_name, top1_diff_volume, top1_diff_amount |
| [f03-tvapis.futures-op.com/history](https://f03-tvapis.futures-op.com/history) | `symbol=TX-1#goodway_chip960; resolution=1; from=Unix秒; to=Unix秒; agent=daddy960; product=web_goodway` | bars, metaData |
| [market-data-api.futures-ai.com/chip960_tradeinfo/](https://market-data-api.futures-ai.com/chip960_tradeinfo/) | `無` | tx_bvav, mtx_bvav, mtx_tbta |
| [storage.googleapis.com/symbol-config/common_symbol_list.json](https://storage.googleapis.com/symbol-config/common_symbol_list.json) | `無` | TW50, TW100M, OTC50, osFuturesList, osStockList, stockCategory, stockFutures, twse … |
| [storage.googleapis.com/symbol-config/contract_info.json](https://storage.googleapis.com/symbol-config/contract_info.json) | `無` | YM-1, NQ-1, ES-1, RTY-1, C-1, S-1, BO-1, W-1 … |
| [tv-signalr.futures-op.com/history](https://tv-signalr.futures-op.com/history) | `symbol=TX-1#goodway_chip960; resolution=1; from=Unix秒; to=Unix秒; agent=daddy960; product=web_goodway` | bars, metaData |
| [tvapi.futures-ai.com/history](https://tvapi.futures-ai.com/history) | `symbol=TX-1; resolution=5; from=Unix秒; to=Unix秒` | t, o, h, l, c, v, s |
| [tvapi.futures-op.com/config](https://tvapi.futures-op.com/config) | `無` | supports_search, supports_group_request, supports_marks, supports_timescale_marks, supports_time, symbols_types, supported_resolutions, timezone |
| [tvapi.futures-op.com/history](https://tvapi.futures-op.com/history) | `symbol=2330#big3_net_stock; resolution=1D; from=Unix秒; to=Unix秒; agent=daddy960; product=web_goodway` | t, o, h, l, c, v, s |
| [tvapi.futures-op.com/search](https://tvapi.futures-op.com/search) | `query=TX-2; exchange=; type=all_types` | 陣列：symbol, full_name, type, description, exchange |
| [tvapi.futures-op.com/symbols](https://tvapi.futures-op.com/symbols) | `symbol=TX-2` | exchange, timezone, session, has_intraday, has_daily, supported_resolutions, pricescale, minmov … |
| [tvapi.futures-op.com/time](https://tvapi.futures-op.com/time) | `無` | number |

</details>

## 設定環境變數
```bash
建立 `.env`，並填入：
API_ID=你的 api_id
API_HASH=你的 api_hash
```

Telegram relay 也會同步監控群益顧問下單機的 `Bridge_Lite_Signal.log`。在 Docker
環境中，將 `CONSULTANT_SIGNAL_LOG_DIR` 設為包含該檔案的主機資料夾，例如：

```text
CONSULTANT_SIGNAL_LOG_DIR=C:/Program Files (x86)/MR.AutoTrading/群益期貨顧問策略下單機/Log
CONSULTANT_SIGNAL_LOG_PATH=/external/consultant-log/Bridge_Lite_Signal.log
CONSULTANT_SIGNAL_LOG_ENCODING=cp950
```

服務會從目前檔案尾端開始監控，並把讀取位置保存到
`telegram-relay-records/consultant_signal_log_checkpoint.json`。Telegram 與本機日誌
同一策略已經到達目標倉位後，再收到相同的倉位轉換會視為重送；策略先離開目標倉位，
之後再次走相同轉換才會視為新訊號。兩個來源的觀測仍會保留在
`telegram_signal_events.jsonl`。

## 開啟瀏覽器
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
--remote-debugging-port=9222 \
--remote-allow-origins='*' \
--user-data-dir="$HOME/chrome-debug"


# 手動補跑技術資料
cd /Users/kt/Desktop/self/monitor/backend-futures-py
.venv/bin/python fetch_stock_tech.py --min-count 3 --report-date YYYY-MM-DD --output-date YYYY-MM-DD --sleep 0.2

# 手動規格化某天報告
cd /Users/kt/Desktop/self/monitor/frontend-vue
PATH=/Users/kt/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH pnpm institutional:normalize 2026-05-08

# 每日完整產出順序
cd /Users/kt/Desktop/self/monitor/backend-futures-py
.venv/bin/python fetch_stock_tech.py --min-count 3 --report-date YYYY-MM-DD --output-date YYYY-MM-DD --sleep 0.2

cd /Users/kt/Desktop/self/monitor/frontend-vue
PATH=/Users/kt/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH pnpm institutional:normalize YYYY-MM-DD

# 自動化單一入口
cd /Users/kt/Desktop/self/monitor
bash scripts/build-institutional-report.sh YYYY-MM-DD

# 使用 $signal-replay-backtest 回測目前策略
