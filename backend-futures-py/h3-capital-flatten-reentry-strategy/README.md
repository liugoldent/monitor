# 群益 H3 微型台指策略

## 新電腦設定

將 .env.example 複製為 .env 並填入帳密；將 h-config.example.json 複製為 h-config.json，核對目前有效合約。官方下載套件的 SKDLLPython.py 及 libs/ 須放入本目錄，這些SDK檔案不隨Git提交。使用64位元Windows Python；Windows啟動腳本目前採用本機Codex內建Python，其他電腦須核對該執行環境路徑。

## 啟動與停止

雙擊根目錄 run-windows-services.cmd，透過 scripts/windows/capital-h3-service.ps1 啟動本機 h_monitor.py --live --service。群益 DLL 在 Windows 原生 Python 執行。重複啟動會核對既有程序，避免第二個交易程序。

根目錄停止腳本會停止此服務。關閉日誌視窗不會停止策略；停止程序不會自動平倉或撤銷委託。

## 設定與規則

本目錄 .env 提供 CAPITAL_LOGIN_ID、CAPITAL_PASSWORD、CAPITAL_ACCOUNT_1、CAPITAL_ACCOUNT_2；CAPITAL_ACCOUNT_SELECT=1 或 2 指定帳戶。服務啟動需預先填妥設定，不使用永豐 API_KEY3。

h-config.json 指定合約、固定進場1口及 Relay 路徑。現在設定202610微台，到期前須核對並更新合約；沒有自動換月。

訊號解析與可信來源規則統一共用 `../h_signal.py`，Relay 紀錄端與永豐下單端也使用同一模組。只接受 Relay received、h路由、sender_username=taiwan_mxf_bot 的浩克3訊號通知。支援多X口／空X口及 `(B=X S=Y)`：B>0、S=0 為多；B=0、S>0 為空；數量不改變進場口數，方向不明或互相矛盾則跳過。每筆新訊號先確認平倉完成，再依多空進場1口，市價IOC、非當沖。沿用10秒同方向去重、相同訊息ID不重送。

未來在本目錄擴充其他帳號時，沿用 `h_core.parse_signal()` 取得共用方向，不再新增各帳號的訊號解析規則。帳號連線、委託、庫存核對與交易狀態由各帳號各自處理；每個帳號必須使用獨立 Store、委託 guard 與鎖檔，讓同一訊號能各自執行一次。現有服務仍只執行 CAPITAL_ACCOUNT_SELECT 指定的帳號，這次不會新增啟動其他帳號。

啟動及重啟從檔尾開始，不追補歷史或停機訊號。群益版另拒絕超過30秒訊號；委託不明、部分成交、斷線或庫存無法確認時停止，不重送。没有每日固定清倉。原永豐H3仍運作，兩者使用不同券商帳戶。

## 檔案

- h_monitor.py：Relay監控與服務入口。
- h_core.py：訊號、去重、狀態及先平後開。
- capital_broker.py：群益連線、庫存、委託與回報。
- local_env.py：帳密設定讀取。
- SKDLLPython.py、libs/：官方SDK與DLL相依檔。
- test_h_execution.py：離線回歸測試。
- runtime/：狀態、未確認委託、日誌及實單測試紀錄。

服務日誌為 runtime/windows-service.log，錯誤為 runtime/windows-service-error.log。帳密、DLL與runtime列入Git忽略。不要刪除交易狀態來解除未確認委託阻擋，須先核對券商庫存與委託。

## 驗證

2026-10-05已驗證登入、Proxy target=4、合約查詢，以及兩個帳戶的202610微台買進1口市價IOC實單測試。均收到 [060] 保證金不足拒單，回查沒有新增庫存。完整成交後的庫存格式與回報映射仍待聯調，拒單測試不代表完整成交流程已驗證。

從專案根目錄執行既有測試：

```powershell
& 'C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s backend-futures-py/h3-capital-flatten-reentry-strategy -p test_h_execution.py -v
```
