# 2026-10-16 H3 修復進場規則

常態 `h_monitor.py` 已改用共用 `../h3_recovery_sizing.py`：固定單口已平倉
淨損益回撤點數／訊號時指數。第1口從新H3訊號持續跟隨，不等回撤；
只有第2口等待先達2.5%再修復至≤1.25%。
首次那一輪不加碼；先回前高，後續新一輪符合相同條件才進2口，歸零回1口。
單口持續跟隨，上限2口。啟動不下單，必須等新H3換向訊號；第1口沒有回撤門檻。
H3 Discord由Relay提醒；訊號行情缺失或舊倉與新帳本不符時停止核對。
群益專用的啟動、成交、拒單與異常通知使用上層 `backend-futures-py/.env` 的
`DISCORD_CAPITAL_H3_MDD_ADD_URL`，不載入其他券商帳密；`--check-config` 會驗證此設定。
通知加上「群益」標籤，HTTP失敗只記錄錯誤類型，不影響成交流程或重送委託。
成功／失敗保存於SQLite audit的 `capital_discord_delivered`／`capital_discord_failed`。
新狀態保存於原SQLite的 `h3_recovery_state`，未確認委託保護與先平後開流程保留。
歷史基準建立方式見上層README；每日重啟不重置單口前高、1／2口階段或首次回前高的進度。

以下較早記錄中的固定每筆1口說明，僅描述舊版行為。

# 群益 H3 微型台指策略

## 新電腦設定

將 .env.example 複製為 .env 並填入帳密；將 h-config.example.json 複製為 h-config.json，核對目前有效合約。官方下載套件的 SKDLLPython.py 及 libs/ 須放入本目錄，這些SDK檔案不隨Git提交。使用64位元Windows Python；Windows啟動腳本目前採用本機Codex內建Python，其他電腦須核對該執行環境路徑。

## 啟動與停止

雙擊根目錄 run-windows-services.cmd，透過 scripts/windows/capital-h3-service.ps1 啟動本機 h_monitor.py --live --service。群益 DLL 在 Windows 原生 Python 執行。重複啟動會核對既有程序，避免第二個交易程序。

根目錄統一啟動也會一起啟動凱基H3，兩者均只等待新訊號，不帶測試買單參數。根目錄停止腳本會停止群益與凱基H3。關閉日誌視窗不會停止策略；停止程序不會自動平倉或撤銷委託。

## 設定與規則

本目錄 .env 提供 CAPITAL_LOGIN_ID、CAPITAL_PASSWORD、CAPITAL_ACCOUNT_1、CAPITAL_ACCOUNT_2；CAPITAL_ACCOUNT_SELECT=1 或 2 指定帳戶。服務啟動需預先填妥設定，不使用永豐 API_KEY3。

h-config.json 指定合約、固定進場1口及 Relay 路徑。現在設定202610微台，到期前須核對並更新合約；沒有自動換月。

訊號解析與可信來源規則統一共用 `../h_signal.py`，Relay 紀錄端與永豐下單端也使用同一模組。只接受 Relay received、h路由、sender_username=taiwan_mxf_bot 的浩克3訊號通知。支援多X口／空X口及 `(B=X S=Y)`：B>0、S=0 為多；B=0、S>0 為空；數量不改變進場口數，方向不明或互相矛盾則跳過。每筆新訊號先確認平倉完成，再依多空進場1口，市價IOC、非當沖。沿用10秒同方向去重、相同訊息ID不重送。

未來在本目錄擴充其他帳號時，沿用 `h_core.parse_signal()` 取得共用方向，不再新增各帳號的訊號解析規則。帳號連線、委託、庫存核對與交易狀態由各帳號各自處理；每個帳號必須使用獨立 Store、委託 guard 與鎖檔，讓同一訊號能各自執行一次。現有服務仍只執行 CAPITAL_ACCOUNT_SELECT 指定的帳號，這次不會新增啟動其他帳號。

啟動及重啟從檔尾開始，不追補歷史或停機訊號。群益版另拒絕超過30秒訊號；已明確核對的新單拒絕會保留原因與證據，結束該筆委託並繼續等待新訊號，不重送同一訊號；委託不明、部分成交、斷線或庫存無法確認時仍停止。没有每日固定清倉。原永豐H3仍運作，兩者使用不同券商帳戶。

## 檔案

- h_monitor.py：Relay監控與服務入口。
- h_core.py：訊號、去重、狀態及先平後開。
- capital_broker.py：群益連線、庫存、委託與回報。
- local_env.py：帳密設定讀取。
- SKDLLPython.py、libs/：官方SDK與DLL相依檔。
- test_h_execution.py：離線回歸測試。
- runtime/：狀態、未確認委託、日誌及實單測試紀錄。

服務日誌為 runtime/windows-service.log，錯誤為 runtime/windows-service-error.log。帳密、DLL與runtime列入Git忽略。不要刪除交易狀態來解除未確認委託阻擋，須先核對券商庫存與委託。

## 庫存查詢與拒單恢復

所有庫存查詢（含成功的查詢）至少間隔6秒，期間持續處理DLL訊息與檢查連線，不沿用前一次庫存。遇到 1019 SK_ERROR_QUERY_IN_PROCESSING 時，每隔6秒重查，含首次最多3次；空白的成功回覆不當成空手。成交後查詢受同一筆委託的30秒確認期限限制，SDK同步回覆逾時也不能確認成交。

只有可關聯本筆 ORKEY 的 [060] Proxy 拒單、相符的新單失敗回報、無成交及最新庫存與送單前相同，或直接關聯的正式新單拒絕回報，才會結束本次guard。整筆平倉拒絕也按相同條件核對：原持倉保留，當筆不另開新倉，繼續等下一個新訊號。結束與證據在同一SQLite交易保存；訊號仍維持已消耗，不會重送。Gateway錯誤、缺回報、未知結果、無法確認的平倉失敗及部分成交仍保留guard並停止。

### 查詢與核對10月12日的舊拒單

可直接在 PowerShell 執行（路徑含空白也適用）：

~~~powershell
# 唯讀登入、核對合約、回補、庫存與本機guard；不送單
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\USER\Desktop\monitor\scripts\windows\capital-h3-service.ps1" -Action Diagnose

# 本人核對10/12新單拒絕；不送單，不監控訊號
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\USER\Desktop\monitor\scripts\windows\capital-h3-service.ps1" -Action ReviewPending -RejectionEvidence "C:\Users\USER\Desktop\monitor\backend-futures-py\h3-capital-flatten-reentry-strategy\runtime\read-only-order-check-20261012.json"
~~~

ReviewPending 核對歷史回補的帳號、商品、方向、1口、送單時間及 OrderErr=Y／OrderNo=00000，且最新庫存0口、沒有未結束微台委託。畫面列出證據後，使用者須先在券商端核對並本人輸入指定的「RESOLVE REJECTED 帳號末碼 序號」，才會保留核對紀錄並解除舊guard；工具本身不送單。未確認、證據不符或庫存變動均保留阻擋。不直接刪SQLite、不重播舊訊號。

診斷輸出保存在 runtime/diagnostic.json，含遮蔽帳號後的微台回補資料；可用作後續核對入口的 -RejectionEvidence。若回補已不含那筆歷史單，不能用空回補推定舊單已拒絕；須使用當時保存的證據並本人核對。

### 本人單次正式送單測試

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\USER\Desktop\monitor\scripts\windows\capital-h3-service.ps1" -Action TestBuyOne
~~~

若有舊guard，TestBuyOne 會先顯示並核對已保存的10/12拒單證據，要求本人確認解除；成功後才進入新測試單的另一個確認畫面。取消任一步驟都不送新單。證據不符或缺漏仍停止；其他證據可用 -RejectionEvidence 指定。這個模式先完成唯讀登入與庫存查詢，不會在顯示舊單核對畫面前就因guard退出。一般背景 Start 仍保留未確認委託阻擋。

僅本人在互動終端操作，畫面核對正式帳號末碼、當期合約後，輸入指定的「BUY 1 合約 MKT IOC 帳號末碼」才送出微型台指買進1口、市價IOC、新倉、非當沖。須空手且無未確認委託；送出前持久化一次性嘗試紀錄，成功、拒單或中斷後都不因重啟再送。以成交回報及最新庫存共同確認；測試完成即退出，不會自動平倉或開始H3監控。開發驗證不代替使用者執行正式委託。

完成舊單核對後，一般背景策略使用：

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\USER\Desktop\monitor\scripts\windows\capital-h3-service.ps1" -Action Start
~~~

## 驗證

2026-10-15 已以正式帳號末碼2899進行唯讀登入，通過商品核對及委託回補，連續兩次查到微台庫存0口，無未結束微台委託。權益、可用餘額均為0；查詢成功不代表具備下單保證金。修正後的 PowerShell Diagnose 入口也完成實際查詢。後續使用者已本人完成舊拒單核對，並送出一筆新的1口正式MKT/IOC測試，券商回覆 [060] 保證金不足；該筆零成交拒單已依最新庫存核對結案。一般 Start 不看單次測試marker、不再送測試單；開發工具未啟動正式監控。

本機舊guard來自2026-10-12 09:17的一口新倉買單；當時唯讀回補證據保存於 runtime/read-only-order-check-20261012.json，OrderErr=Y、OrderNo=00000，券商原因為 [060] 保證金不足。舊程式將拒單保留為未確認委託，造成10/13、10/14、10/15啟動持續被擋。舊資料缺少完整ORK/SeqNo關聯，該筆已由使用者透過本人核對入口結案，歷史稽核紀錄保留。

2026-10-05兩個帳戶及10-12策略均曾實際呼叫相同 SendFutureProxyOrder API，收到券商保證金不足拒單。這證明曾送達券商並收到拒單，不能當成實際成交驗證。完整成交後的非空庫存格式仍待使用者在有足額保證金時聯調；程式遇到未支援格式會停止核對。

離線測試涵蓋先平後開、市價IOC、新平倉、可信訊號與去重、庫存查詢節奏／1019／逾時、可確認拒單恢復、未知結果保留guard、本人核對舊拒單、取消與證據不符、單次正式測試的互動及防重送。測試中的送單全部使用替身，不呼叫券商下單API。

~~~powershell
& 'C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s backend-futures-py/h3-capital-flatten-reentry-strategy -p 'test_*.py' -v
~~~
