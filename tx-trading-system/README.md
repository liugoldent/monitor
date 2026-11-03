# TX Trading

React + Vite + TradingView Lightweight Charts 的本機行情工作站。上方導覽提供「行情圖表」與「原始資料」兩個路由。

視覺依 [Apple Design Skill](https://github.com/dickwu/apple-design-skill/blob/main/SKILL.md) 的排版、色彩、圖表與無障礙原則調整為簡潔的市場工具：使用系統字體、分段導覽、語意色彩與留白。Apple 裝置使用系統 SF 字體，Windows 使用 Segoe UI 與微軟正黑體，不額外下載字型。自動跟隨系統深淺色，圖表同步換色；支援減少動態、減少透明度及提高對比偏好。這是 React 網頁的設計調整，沒有模仿 macOS 視窗控制或宣稱使用原生元件。

## 啟動

需要 Node.js 20.19+ 或 22.12+。

```powershell
cd C:\Users\USER\Desktop\monitor\tx-trading-system
npm install
npm run dev
```

開啟 http://localhost:5173 。預設只綁定本機 127.0.0.1。可用環境變數 `PORT` 修改連接埠。

正式模式：先 `npm run build`，再 `npm start`。仍須透過 Node 伺服器啟動，才能讀取 CSV；不能直接打開 dist/index.html。

也已整合到專案根目錄的 Windows 啟停工具：雙擊 `C:\Users\USER\Desktop\monitor\run-windows-services.cmd` 會隨原有服務安裝所需套件、建置並在背景啟動前端；雙擊 `stop-windows-services.cmd` 會一起停止。啟動後開啟 http://127.0.0.1:5173 。啟動器會記錄前端 PID、建立時間與絕對執行路徑，停止時只關閉驗證為本專案的程序。記錄與輸出位於 `.runtime/service.json`、`.runtime/stdout.log`、`.runtime/stderr.log`（不加入 Git）。若先前用 `npm run dev` 手動啟動，請先在該終端機按 Ctrl+C 釋放 5173，避免和背景服務衝突。原有 `-NoPull` / `-NoBuild` 參數仍可用；`-NoBuild` 需要已安裝套件及已產生的 dist。

Docker 的 `monitor-app:local` 僅建置後端服務，不再依賴已移除的 `frontend-vue`。React 專案與其本機執行資料不加入 Docker 建置內容；5173 由 Windows 前端程序使用。

## 資料與對齊

- 伺服器直接唯讀取用相鄰目錄 `../backend-futures-py/tv_doc/webhook_data_1min.csv` 及 `mxf_value.csv`，不複製、不修改來源。
- K 棒使用 `TradingView Time`，籌碼使用 `time`。兩者假設為台北時間 UTC+8，以同分鐘精確比對；不使用接收時間 `Record Time`。
- 圖表連續載入截至所選日期的最近 10,000 個時間點，包含前幾日的日、夜盤。最新模式跨午夜會延續同一條時間軸。日期選擇、當日統計與原始資料頁仍按日曆日期 00:00–23:59 篩選，並非期交所交易日歸屬。
- 時間軸取兩檔的聯集，帶秒數的歷史籌碼資料向下歸入所屬分鐘（如 12:03:01 → 12:03:00），原始時間保留於 API 的 chip.sourceTime。缺少一整筆分鐘資料時，最多沿用該來源上一筆真實資料 2 分鐘，K 棒 OHLC、均線與籌碼數值直接複製；不使用未來資料，不將補值繼續當成來源延長。原始列內個別欄位的空值仍保留。兩檔同時缺少 1～2 分鐘時也補上時間點，長時間的休盤間隔不補。原始資料頁以「沿用前值」標記補值列；API 補值物件包含 imputed 與 imputedFrom，來源 CSV 不修改。真實資料補到後會取代補值。重複分鐘取最後一筆。無效或尚未寫完的列略過，統計可在原始資料頁查看。
- 上圖：一分 K 棒（紅漲綠跌）及 CSV 既有五條 MA。下圖：坦克 tx_bvav 柱狀、游擊 mtx_bvav、炮灰 mtx_tbta、游擊平均 mtx_bvav_avg。均線不在前端重算。
- 共用圖表的兩個 pane，縮放、水平捲動、十字線與時間軸同步。點圖例可切換線條，拖曳分隔線可調整上下比例。
- 預設顯示最近 180 個時間點，可切換 120、240 或當日全部。
- 在右端觀看時，新增分鐘會向右延伸並自動跟隨，保留縮放比例與右側空間。向左拖曳查看歷史時不會被自動更新拉回，且以時間錨點保留視角。按「回到最新」恢復跟隨，也會退出固定歷史日期。跨日更新不會重設圖表。
- 預設每 30 秒重新讀取 CSV，可暫停或手動更新；檔案未變更時使用伺服器記憶體快取。這是 CSV 快照，不是交易所即時行情串流。
- `GET /api/market?date=2026-11-02` 提供所選日期的對齊資料，省略 date 則取最新 K 棒日期。
- 加上 `history=1` 時額外回傳 `historyRows`，提供連續圖表的最近 10,000 個時間點；`rows` 與 `stats` 仍為所選當日資料。

## 策略看板

`/strategies` 直接唯讀取用 `../backend-futures-py/tv_doc/six_strategy_signal_events.csv`，每 30 秒更新。`GET /api/strategies` 提供各策略狀態、摘要及歷史事件。

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
