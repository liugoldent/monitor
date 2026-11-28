# TX Trading

React + Vite + TradingView Lightweight Charts 的行情工作站。行情與策略 API 從 MongoDB Atlas 讀取資料；本機收資料服務仍保留 CSV。

視覺依 [Apple Design Skill](https://github.com/dickwu/apple-design-skill/blob/main/SKILL.md) 的排版、色彩、圖表與無障礙原則調整為簡潔的市場工具：使用系統字體、分段導覽、語意色彩與留白。Apple 裝置使用系統 SF 字體，Windows 使用 Segoe UI 與微軟正黑體，不額外下載字型。自動跟隨系統深淺色，圖表同步換色；支援減少動態、減少透明度及提高對比偏好。這是 React 網頁的設計調整，沒有模仿 macOS 視窗控制或宣稱使用原生元件。

## 啟動

需要 Node.js 20.19+ 或 22.12+。

```powershell
cd C:\Users\USER\Desktop\monitor\tx-trading-system
npm install
npm run dev
```

開啟 http://localhost:5173 。預設只綁定本機 127.0.0.1。可用環境變數 `PORT` 修改連接埠。

正式模式：先 `npm run build`，再 `npm start`。本機 Node 伺服器會從相鄰 `backend-futures-py/.env` 讀取 `MONGO_URI`；Vercel 部署時須在專案環境變數設定 `MONGO_URI`，且 Vercel 專案 Root Directory 設為 `tx-trading-system`。連線字串不可提交到 Git。

網站不要求存取碼。行情、原始資料與財神／智能策略公開讀取 MongoDB，按畫面每 30 秒更新；圖表與策略頁的 H2／H3 訊號、方向、進場價及浮動點數只使用至少 30 分鐘前的資料。延遲限制在 MongoDB 查詢中執行，不能透過修改日期參數略過。讀取 API 每個函式實例對相同查詢保留 30 秒結果，公開結果也可由 CDN 快取 30 秒；Vercel 防火牆另限制每 IP 每分鐘 30 次 `/api/` 請求。Vercel 函式部署在香港 `hkg1`，與 Atlas 叢集同區。

也已整合到專案根目錄的 Windows 啟停工具：雙擊 `C:\Users\USER\Desktop\monitor\run-windows-services.cmd` 會隨原有服務安裝所需套件、建置並在背景啟動前端；雙擊 `stop-windows-services.cmd` 會一起停止。啟動後開啟 http://127.0.0.1:5173 。啟動器會記錄前端 PID、建立時間與絕對執行路徑，停止時只關閉驗證為本專案的程序。記錄與輸出位於 `.runtime/service.json`、`.runtime/stdout.log`、`.runtime/stderr.log`（不加入 Git）。若先前用 `npm run dev` 手動啟動，請先在該終端機按 Ctrl+C 釋放 5173，避免和背景服務衝突。原有 `-NoPull` / `-NoBuild` 參數仍可用；`-NoBuild` 需要已安裝套件及已產生的 dist。

Docker 的 `monitor-app:local` 僅建置後端服務，不再依賴已移除的 `frontend-vue`。React 專案與其本機執行資料不加入 Docker 建置內容；5173 由 Windows 前端程序使用。

## 資料與對齊

### MongoDB 圖表資料鏡像

Docker 的 `webhook-server`、`monitor-mxf`、`telegram-signal-relay` 會在收到新資料時，將原本寫入 CSV 的資料同步 upsert 至 `.env` 中 `MONGO_URI` 指向的 Atlas，資料庫為 `tx_trading_system`：

| 集合 | 來源 | 用途 |
| --- | --- | --- |
| `candles_1m` | TradingView 一分 K webhook | K 線與均線 |
| `chips_1m` | MXF 監控計算後的快照 | 坦克、游擊、炮灰、游擊平均、訊號與趨勢 |
| `strategy_signals` | EF 策略 CSV | 策略看板進出場 |
| `ef_position_events` | EF 部位事件 CSV | EF 進出場原始事件 |
| `h_position_events` | H 部位事件 CSV | H 進出場原始事件 |
| `h_trades` | H 交易 CSV | H 歷史進出場 |
| `h2_trades` | H2 交易 CSV | H2 單口策略歷史進出場，與 H 分開儲存 |

寫入使用固定 `_id` 做 upsert，重送同一根 K 棒不會增加重複文件；EF 策略事件依原始 CSV 順序保留每一筆。Telegram relay 啟動及收到 H2 訊號後，會同步整份 `h2_trade.csv` 到 `h2_trades`，補回先前因 MongoDB 暫時無法連線而缺少的紀錄。寫入時若日期變更或距離上次清理超過一小時，會檢查保留範圍：**只有 `candles_1m` 與 `chips_1m` 保留最近 10 個平日交易日期**，並保留其週五夜盤跨到週六凌晨的資料。EF/H/H2 策略與交易紀錄保留全部歷史，不做日期清理。清理僅作用於 `tx_trading_system`，不會修改既有 `mxf_futures`。MongoDB 寫入或清理失敗會記錄到服務日誌，但不停止 webhook、MXF 或 Telegram 服務；CSV 繼續寫入。

可重跑的歷史補資料指令對 K 線與 MXF 預設只處理最近 10 個交易日，對 EF/H 則匯入全部可用歷史。先乾跑確認筆數：

```powershell
python backend-futures-py/backfill_chart_mongo.py --dry-run
python backend-futures-py/backfill_chart_mongo.py
```

本機與 Vercel 的 `/api/market`、`/api/strategies` 已改查 MongoDB。首次開圖表先查所選日期資料並顯示，歷史資料稍後補載；只有歷史模式才查詢截至所選日期的最近 10,000 筆來源資料。畫面每 30 秒輪詢一次當日資料。

- 收資料程式將 `../backend-futures-py/tv_doc/webhook_data_1min.csv` 及 `mxf_value.csv` 同步至 Atlas；圖表 API 唯讀查詢 Atlas。
- K 棒使用 `TradingView Time`，籌碼使用 `time`。兩者假設為台北時間 UTC+8，以同分鐘精確比對；不使用接收時間 `Record Time`。
- 圖表在當日資料出現後補載截至所選日期的最近 10,000 個時間點，包含前幾日的日、夜盤。最新模式跨午夜會延續同一條時間軸。日期選擇、當日統計與原始資料頁仍按日曆日期 00:00–23:59 篩選，並非期交所交易日歸屬。
- 時間軸取兩檔的聯集，帶秒數的歷史籌碼資料向下歸入所屬分鐘（如 12:03:01 → 12:03:00），原始時間保留於 API 的 chip.sourceTime。缺少一整筆分鐘資料時，最多沿用該來源上一筆真實資料 2 分鐘，K 棒 OHLC、均線與籌碼數值直接複製；不使用未來資料，不將補值繼續當成來源延長。原始列內個別欄位的空值仍保留。兩檔同時缺少 1～2 分鐘時也補上時間點，長時間的休盤間隔不補。原始資料頁以「沿用前值」標記補值列；API 補值物件包含 imputed 與 imputedFrom，來源 CSV 不修改。真實資料補到後會取代補值。重複分鐘取最後一筆。無效或尚未寫完的列略過，統計可在原始資料頁查看。
- 上圖：一分 K 棒（紅漲綠跌）及 CSV 既有五條 MA。下圖：坦克 tx_bvav 柱狀、游擊 mtx_bvav、炮灰 mtx_tbta、游擊平均 mtx_bvav_avg。均線不在前端重算。
- 圖表上的「策略點位」只顯示至少延遲 30 分鐘的 h2、h3 交易紀錄進出場。向上標記代表多單，向下標記代表空單；文字標明策略及「進／出」。H2 原始微秒時間在 API 截至秒，兩種策略的進出場分開顯示；同一分鐘同策略、同動作、同方向的事件合併計數。標記依事件時間貼近真實一分 K 棒，圖表只在分鐘位置顯示，不宣稱秒級 K 棒定位。`GET /api/chart-signals?date=YYYY-MM-DD&from=YYYY-MM-DD` 提供截至 30 分鐘前的精簡事件與方向。
- 共用圖表的兩個 pane，縮放、水平捲動、十字線與時間軸同步。點圖例可切換線條，拖曳分隔線可調整上下比例。
- 預設顯示最近 180 個時間點，可切換 120、240 或當日全部。
- 在右端觀看時，新增分鐘會向右延伸並自動跟隨，保留縮放比例與右側空間。向左拖曳查看歷史時不會被自動更新拉回，且以時間錨點保留視角。按「回到最新」恢復跟隨，也會退出固定歷史日期。跨日更新不會重設圖表。
- 預設每 30 秒重新讀取 MongoDB，可暫停或手動更新。這是收資料服務寫入後的快照，不是交易所即時行情串流。
- 行情總覽顯示最後一根 K 線的台北時間與距現在多久；相對時間每 30 秒更新。畫面的資料來源標示為 MongoDB，不再沿用「本機資料／CSV 已載入」舊文案。
- `GET /api/market?date=2026-11-02` 提供所選日期的對齊資料，省略 date 則取最新 K 棒日期。
- 加上 `history=1` 時額外回傳 `historyRows`，提供連續圖表的最近 10,000 個時間點；`rows` 與 `stats` 仍為所選當日資料。

## 策略看板

`/strategies` 每 30 秒更新。訪客可直接查看從 `h2_trades`、`h_trades` 推算的 30 分鐘延遲 H2／H3 方向、進場價與浮動點數，以及 `strategy_signals` 中財神／智能的即時狀態、摘要及歷史事件。`GET /api/strategies` 的 `hPositions` 固定只回傳延遲資料。

- H 系列獨立顯示 h2、h3 在延遲截止時間的多單、空單、空手或無紀錄，並獨立統計多空策略數；不併入財神／智能的淨倉位或策略數。方向以截止時間前各自最新交易紀錄的進場／出場推算。
- H2/H3 開倉時顯示 `h2_trades`／`h_trades` 的虛擬進場價，浮動點數使用同樣至少延遲 30 分鐘的 `MXF1!` K 線收盤價作基準；財神／智能訊號本身沒有成交價，開倉時以訊號收到前 120 秒內已寫入 MongoDB 的最近一根 K 線收盤價作進場參考，浮動點數使用最新有效 K 線。未取得可靠進場參考價時顯示「—」；不計費用、滑價或加減碼後的成本，與券商實際損益不同。
- 看板標示 H2/H3 的延遲截止時間，以及各策略最後交易／訊號的時間及距今多久，超過 24 小時的策略時間以提醒色標示。H2/H3 卡片標示「紀錄價」；財神／智能標示「K 線估算價」，因沒有成交價，已平倉點數顯示「—」。

- 按 `strategy_code` 分組，依 CSV 寫入順序取最後一筆有效 `new_position` 判定目前多單、空單或空手。這是策略訊號狀態，不代表券商即時庫存。檔案目前不只六個策略，會動態顯示全部已出現的策略。
- 合計為每個策略最新帶正負號的倉位之和，並列出多空策略數與多空倉位總量；不累加歷史 `quantity`。反手從 +1 到 -1 的結果是空 1，不是再減一筆訊號單位。
- 點擊卡片有翻面回饋，並開啟歷史紀錄對話框。可查看進場、出場、反手、倉位變化及原始訊號，支援分頁、Escape 關閉與鍵盤操作。尊重減少動態偏好。
- 歷史依 CSV 最後寫入在前。時間優先採 `message_time`，其次 `received_at`；兩者皆缺少時顯示「時間未記錄」。無效或不完整列略過，不把缺值當作空手。來源沒有成交價，因此不計算或虛構損益。

## 驗證

```powershell
npm test
npm run build
```

介面依使用者提供的截圖製作；參考站登入後的私有內容未存取。圖表由 [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) 提供。
