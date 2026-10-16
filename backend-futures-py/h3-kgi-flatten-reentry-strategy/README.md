# 2026-10-16 H3 修復進場規則

常態 `kgi_monitor.py` 已改用共用 `../h3_recovery_sizing.py`，與群益同一條
固定單口已平倉影子曲線。第1口從新H3訊號持續跟隨，不等回撤；
先回前高，後續新一輪回撤點數／訊號時指數達2.5%再修復至≤1.25%才2口；
歸零恢復1口，持續跟隨。每日重啟保留帳戶階段、持倉帳本與單口曲線，不重置。
啟動不下單、不補歷史訊號；真正下單仍須有新換向訊號與有效行情。
新帳本 `h3_recovery_state` 與委託guard同一SQLite成交提交；舊金額回撤帳本
保留供核對，不再決定口數。舊帳本仍有倉位或券商庫存不符時不自動移轉。
`KGI_UNIT=1`；新模式停用啟動即買進測試選項，避免繞過等待新H3訊號的規則。
Windows Stop同時停止venv啟動器與工作程序，Start也會辨識尚存的工作程序，避免重複執行。
H3 Discord由Relay發修復提醒，既有凱基Discord仍發成交與異常通知。
歷史基準建立方式見上層README。下方舊3%加碼與啟動測試說明屬舊版記錄。

# 凱基 H3 微型台指回撤3%加一口策略

沿用群益 `../h3-capital-flatten-reentry-strategy` 的 `Store`、訊號解析與去重規則；凱基的 `mdd_sizing.py` 負責獨立回撤帳本、先平後開與通知，不修改群益服務。每筆可信、30秒內的浩克3新訊號，先平掉已登錄成本的持倉，確認完整成交且庫存歸零，結算損益後再依訊號方向開新倉。即使同方向也先平再開。市價 IOC、非當沖；明確指定平倉 PF=1、新倉 PF=0。

只讀 Relay 的 received / h / taiwan_mxf_bot 訊號；支援多X口、空X口、(B=X S=Y)，訊號數量不影響進場口數。沿用相同訊息 ID 與 10 秒同方向去重。啟動、重啟、訊號檔輪替均從檔尾開始，不補歷史。不設每日固定清倉。

## 固定口數與回撤規則

1. 第一次啟用從空手開始，累計權益 `E=0`、高水位 `H=0`，直接跟新H3訊號，基礎1口。不匯入H3 CSV舊績效，不等待首筆獲利。
2. 只用這個凱基帳號由本程式確認的完整成交。每筆平倉後，以實際加權成交均價、方向及該筆實際口數計算損益；每口每點10元，扣每口單邊成本估算（預設24元）的兩邊。
3. 更新 `E=累計已平倉估算淨損益`、`H=max(舊H,E)`、`D=H-E`。回撤比例 `R=D / 10 / 當次平倉加權均價`，用期貨成交價近似指數。例：平倉價50,000點，回吐15,000元即3%。不是帳戶本金損失3%。
4. 基礎階段 `R >= 3%` 時，**下一筆新倉改為2口**；加碼階段只要 `D>0` 就維持2口，即使R回落到3%以下。繼續虧損最多仍是2口。
5. 當已平倉權益回到原高水位或創新高，`D=0`，**下一筆改回1口**。使權益恢復的那筆仍按原進場時鎖定的2口結算。
6. 口數不在持倉途中變動。同一訊號先平倉、更新階段，再以更新後的1／2口開反向或同向新倉。

2口的損益按2倍計入本帳號E，並非一口H3虛擬基準線。未平倉浮盈虧、入出金與其他帳號不計入。高水位、階段、進場均價、口數及成交識別保存在各帳號／測試環境自己的SQLite，跨日與重啟延續。成交入帳與清除委託guard在同一SQLite交易中完成，避免重複結算。

第一次升級若券商已有未登錄成本的持倉，或重啟時庫存與帳本不符，程式停止核對，不猜成本、不自動接管舊倉。未完整成交、缺成交價或時間、未知委託時保留guard，不重送，也不開下一筆。

Discord從 **`backend-futures-py/.env` 的 `DISCORD_KGI_H3_MDD_ADD_URL`** 讀取。通知啟動、每次平倉／進場確認、達3%加至2口、回撤歸零恢復1口及異常停止；包含E/H/D、最後平倉回撤比、下一筆口數與估算成本。HTTP失敗只記錄錯誤類型，不重送交易；通知在交易流程後送出，避免網路等待卡住先平後開。

## 安裝與設定

在專案根目錄 PowerShell 執行：

```powershell
.\scripts\windows\setup-kgi-h3.ps1
```

腳本建立本目錄獨立 `.venv`，安裝 `kgisuperpy==2.1.2`，不改群益或永豐的 Python 環境。其他電腦可用 `-PythonPath 'C:\Python312\python.exe'` 指定 64 位元 Python。凱基服務權限與有效憑證須先開通。

本目錄 `.env` 填入以下資料，勿填在程式、JSON 或命令列：

```dotenv
KGI_LOGIN_ID=
KGI_PASSWORD=
KGI_ACCOUNT=
KGI_BROKER_ID=
KGI_SIMULATION=true
KGI_UNIT=1
KGI_MDD_ONE_WAY_COST_TWD=24
```

ACCOUNT 是期貨帳號；BROKER_ID 是登入帳戶清單中的期貨分公司代碼（保留前導零）。帳號及分公司會與凱基登入帳戶清單核對。`true` 連測試環境；填 `false` 才連正式環境。既有環境變數優先於 `.env`。

這個版本的 `KGI_UNIT` **必須為1**，其他值會在設定檢查階段拒絕。1／2口由上述回撤狀態決定；B/S只決定方向，不乘上訊號數量。`KGI_MDD_ONE_WAY_COST_TWD` 是每口單邊成本估算，預設24元，並非已核實的券商費稅；啟用後改成本會停止，須先核對既有損益基準。Discord不讀本目錄.env，也不載入上層其他帳號憑證。

`h-config.json` 預設 TMF / 202610 / TMFJ6，對應群益版月份。須自行核對當期有效契約；無自動換月，月份過期會停止。程式限定TMF，檢查月份與symbol對應，再核對正式／測試帳號與庫存。SDK的 `get_Fcontracts()` 是海外期貨商品表，不能用來核對國內TMF；本機合約檢查不代表券商已接受這個合約的委託。

## 操作

以下從專案根目錄執行。首次填妥設定後先 Check，再 Diagnose：

```powershell
# 本地設定檢查，不登入、不送單
.\scripts\windows\kgi-h3-service.ps1 -Action Check
# 登入及查詢帳戶、商品、庫存，不送單
.\scripts\windows\kgi-h3-service.ps1 -Action Diagnose
# 只預覽新的 H3 訊號，不登入（也可雙擊 run-kgi-h3.cmd）
.\scripts\windows\kgi-h3-service.ps1 -Action Paper
# 背景啟動，依 .env 的測試／正式環境執行新訊號下單
.\scripts\windows\kgi-h3-service.ps1 -Action Start
# 停止凱基策略，不平倉、不撤單
.\scripts\windows\kgi-h3-service.ps1 -Action Stop
```

凱基版已加入根目錄 run-windows-services.cmd，與群益H3一併在Windows原生Python背景啟動；停止總腳本也會停止兩者。統一啟動固定呼叫一般 Start，不附帶任何啟動買單或單次測試參數，啟動後只等待新H3訊號，不補歷史。關閉預覽視窗會停止預覽；關閉日誌視窗不會停止背景服務。

### 手動啟動時先買進1口的模擬測試

環境切換位置是本目錄的 `.env`：`KGI_SIMULATION=true` 為凱基測試環境，`false` 為正式環境；既有同名環境變數優先。啟動測試同時檢查設定與實際登入的broker環境，正式環境會在登入前拒絕這個選項。

```powershell
# 只檢查模擬啟動測試的設定，不登入、不送單
.\scripts\windows\kgi-h3-service.ps1 -Action Check -StartupSimulationBuyOne
# 使用者自行執行：啟動先買進1口微型台指TMF，MKT＋IOC，成交後繼續H3監控
.\scripts\windows\kgi-h3-service.ps1 -Action Start -StartupSimulationBuyOne
```

測試只在空手且回撤口數為1口的階段執行，不平舊倉、不往既有持倉補一口。測試成交會寫入同一個模擬帳號的回撤帳本，後續H3訊號正常管理。每個模擬帳號只執行一次，成功後重啟略過；送單嘗試先持久化，失敗或中斷不自動重送。登入失敗時不會送單；凱基回傳78的登入問題需另行排除。

一般 `-Action Start` 不帶此參數時，仍是等待新H3訊號；`run-kgi-h3.cmd` 仍只開預覽。


### 本人啟動時確認一筆正式測試單

本目錄的 .env 設定為「KGI_SIMULATION=false」、KGI_UNIT=1 後，由本人在 PowerShell 執行以下指令。此模式維持前景互動，不會放到背景服務，也不接受管線代填確認。

~~~powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\USER\Desktop\monitor\scripts\windows\kgi-h3-service.ps1" -Action Start -StartupProductionBuyOne
~~~

程式登入後顯示正式帳號末碼、目前設定合約、買進1口微型台指及 MKT／IOC。核對畫面後，須本人輸入畫面要求的完整文字（例如「BUY 1 TMFJ6 MKT IOC 2248」），才會送出正式委託。取消或輸入不符即停止。這個指令須由使用者自行執行；開發測試不會代送委託。

僅接受空手、帳本沒有未平倉交易及基礎1口階段，不平掉既有部位、不補口。送出前保存一次性嘗試紀錄，成交後寫入同一正式帳號的回撤帳本，接著等待新H3訊號並由策略管理。成功後即使保留參數，重啟也不再買入，只會要求「START KGI H」確認繼續監控；失敗或中斷則停止，須先人工核對券商委託及成交，不自動重送，也不要刪除狀態解鎖。

執行後請保持視窗開啟；Ctrl+C 停止這個前景程式，但不自動平倉或撤單。後續恢復一般背景啟動時，拿掉 -StartupProductionBuyOne 即可；一般 Start 只等待新訊號。只有本地設定檢查可使用「-Action Check -StartupProductionBuyOne」，不登入、不送單。

`runtime/windows-service.log` 與 `windows-service-error.log` 為背景日誌。每個凱基帳號／分公司／測試或正式環境均有獨立 SQLite 狀態與程序鎖，和群益、永豐分開；SDK 下載的憑證、商品與 Token 檔也集中在該帳號的 runtime 子目錄。預覽使用獨立 paper.sqlite，並不消耗正式訊號。狀態會在送單前落盤；能關聯本筆CNT的整筆新單拒絕，且原生回報確認零成交、最新庫存與送單前一致時，保留拒單證據並結束該筆guard，繼續等待下一筆新H3訊號，不重送同一訊號。平倉單被整筆拒絕時保留原持倉，不另開新倉。若已平倉後重開被拒，已結算的損益及回撤階段保留，下一筆新訊號依當時階段進場。部分成交、未知拒單、逾時、斷線、庫存不明、其他月份微台、多空同存、未結束微台委託均停止核對。未確認委託須人工核對券商委託與庫存，不要刪 SQLite 或 guard 解鎖。

登入補檔後及每次庫存查詢之間至少間隔6秒，避免凱基「查詢間隔不足5秒」限制；不沿用舊查詢結果。庫存查詢 `PositionSum()` 回傳 `None`（SDK 等待後仍無回應）或拋出 `TimeoutError` 時，等 6 秒再查，含首次最多查 3 次。每次無回應記錄 `kgi_inventory_query_unavailable`，等待期間持續檢查連線。凱基使用自己的回覆格式，不套用群益的 1019 錯誤碼。只有欄位完整的空表才代表 0 口；不沿用舊庫存，不將無回應當成空手。格式、帳號、合約錯誤及其他例外不重查；仍無回應或斷線就停止。SDK 單次查詢本身也有等待時間，總耗時包含 SDK 等待與兩次重查間隔。

上述重查適用登入檢查、訊號處理、送單前核對及成交後庫存確認。成交後重查受 `confirmation_timeout` 限制；同步SDK查詢若返回時已逾時，也不會確認完成。逾時保留未確認委託guard，不會重送委託或再開新倉。

異常停止通知及 SQLite stopped 紀錄包含程式可辨識的原因、停止階段與本機未確認委託旗標；後者不代表券商即時庫存或委託狀態。SDK／系統例外僅提供錯誤類型及通用說明，不把可能含帳密的原始例外傳到 Discord。

## SDK 適配與驗證範圍

採用使用者提供的 [kgisuperpy 官方套件](https://pypi.org/project/kgisuperpy/)；登入、帳號設定與庫存欄位依 [凱基官方文件](https://superpy.kgieworld.com.tw/kgipythonapi/guide/fut/accountPositionSum)。也已參考本機 `PythonTradeComExample_20230628/TradeComExample_20230628/TradeComFutPySample.py` 的 order() / positionEffect()。

新版公開 create_order 固定自動新平倉（PF=3）；`ExplicitPositionBridge` 保留 SDK 建立 Trade、request ID 及回報映射，只在底層 Order 呼叫替換 PF=1/0。因此鎖定 SDK 2.1.2，更新版本需重新核對底層介面。SDK 庫存無回應是 None，不能當空手；只有具有預期欄位的空表可視為零庫存。需該筆 Trade 完整成交回報與庫存共同確認，不以委託受理視為成交。

離線測試：

```powershell
& .\backend-futures-py\h3-kgi-flatten-reentry-strategy\.venv\Scripts\python.exe -m unittest discover -s backend-futures-py/h3-kgi-flatten-reentry-strategy -p 'test_*.py' -v
```

設定 `KGI_SDK_SOURCE` 為已解壓的 kgisuperpy 套件目錄，可額外以真實 SDK 原始碼做無網路下單參數測試（不載入登入或 DLL）。已完成離線驗證，包括庫存無回應重查、持續失敗不送單、成交後重查不重送及口數。2026-10-15正式環境只讀查詢已驗證連線、登入、指定期貨帳號與帶欄位的空庫存（TMF淨部位0）；測試環境登入先前回傳78。使用者已在2026-10-15正式執行1口TMFJ6買進MKT/IOC測試，並於券商端看到失敗委託。隨後唯讀回補確認原生錯誤碼3296、無成交、庫存0口，舊測試guard已依回補證據結案並保存備份與稽核；測試marker保留，不重送測試單。已驗證正式送達券商及拒單回報，實際成交與非空庫存回應仍待聯調。離線測試不能代替券商聯調。帳密、有效設定、`.venv`、DLL、憑證與runtime均不提交Git。

正常跟單拒單的離線回歸另涵蓋真實SDK的失敗狀態映射（OrderStatus可能維持None）、CNT完整關聯、拒單後下一訊號繼續交易、平倉拒絕不開新倉、拒單零損益、已平倉損益不回滾及舊測試拒單回補核對。統一啟動的驗證以語法解析與啟動參數檢查進行，開發驗證不啟動正式監控或送單。
