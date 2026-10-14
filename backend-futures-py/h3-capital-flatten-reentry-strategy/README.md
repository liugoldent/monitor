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

庫存查詢遇到 `1019 SK_ERROR_QUERY_IN_PROCESSING`（查詢尚未結束）時，每隔 6 秒重查，含首次最多 3 次；等待期間持續檢查連線。只有重新取得有效庫存回覆後才繼續，不沿用舊庫存，也不將 1019 視為 0 口。每次忙碌會記錄 `inventory_query_busy`；仍忙碌、其他查詢錯誤或斷線則停止。這個重查只適用庫存查詢，不重送委託。

## 驗證

已回查2026-10-05 18:47帳號2的一次性測試腳本：呼叫 `SK.SendFutureProxyOrder(broker.login_id, build_order(broker.account, contract, 1, 'entry'))`，與目前自動策略的送單API及委託建構函式相同；都是202610微台買進1口、市價IOC、新倉、非當沖、Proxy target=4。一次性測試與正式策略的差異在回報等待：前者等到新 `OnNewData` 才结束，正式策略原本在 Proxy 拒單後立即退出。現在正式策略在 Proxy 拒單後持續處理訊息，最長30秒內等待同帳戶微台的新單失敗回報，並保存 `proxy_report`、`order_report`（包含時間和序號），缺少後續回報則另記錄 `order_report_timeout`。缺少可直接連結的SeqNo時，回報只作為診斷證據，不確認成交、不清除guard、不重送。此調整已經離線測試，尚未驗證行動贏家的跨平台回報顯示，也未重啟實單服務。

2026-10-12 09:17 的新訊號觸發買進1口下單請求，收到拒單後服務退出；未確認委託成功成立。當日重新只讀登入群益取得伺服器回補，核對完整帳號與末碼2899的指定帳戶一致：回報時間 `20261012 09:17:09`、序號 `2315617343305`、`OrderErr=Y`、`OrderNo=00000`，原因為 `[060] 此投資人個幣保證金不足 不可下單`。指定帳戶的帳戶權益、可用餘額皆為0，微台庫存0口。遮蔽後的回補證據保存於 `runtime/read-only-order-check-20261012.json`。使用者在交易軟體看不到任何回報，該軟體的顯示原因仍未確認；不能僅以保證金不足解釋回報不可見。此次查核沒有送單，未清除未確認委託 guard，也未重新啟動實單服務。補足資金或切換正確帳戶後，仍須核對既有委託狀態。拒單處理現在會將券商原因碼及訊息寫入 `order_rejected` 稽核紀錄和服務日誌，遮蔽登入ID及完整帳號；拒單仍停止、不重送。

2026-10-09以只讀模式查詢指定帳戶，回覆 `001,查無資料,...`，確認當時庫存 0 口；同一連線於 3 秒後重查重現 1019，6 秒後再次查詢恢復為 0。2026-10-08 22:26:02 的訊號已接收，但第一次查庫存即遇到 1019，舊程式直接退出，尚未進入送單階段。舊日誌不足以判定當晚查詢忙碌的上游原因；此次重現驗證了暫時忙碌可恢復，未送出委託。

2026-10-05已驗證登入、Proxy target=4、合約查詢，以及兩個帳戶的202610微台買進1口市價IOC實單測試。均收到 [060] 保證金不足拒單，回查沒有新增庫存。完整成交後的庫存格式與回報映射仍待聯調，拒單測試不代表完整成交流程已驗證。

從專案根目錄執行既有測試：

```powershell
& 'C:\Users\USER\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s backend-futures-py/h3-capital-flatten-reentry-strategy -p test_h_execution.py -v
```
