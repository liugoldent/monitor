# 永豐 2：H3 跟單與連輸六筆加一口

2026-10-29 移除浮盈 500 點啟動／回吐最高浮盈 30% 自動平倉，以及停利後同輪禁止重進限制。尚未加入日 K 均線平倉條件。

使用 `API_KEY2`／`SECRET_KEY2`，2026-10-29 使用者已將 `protection_account.BROKER_SIMULATION` 設為 `False`，改用真實下單；環境變數不覆寫此模式。帳戶與 API_KEY 正式策略隔離。

## 交易規則

1. 啟動後等待康和本機優先／可信 TG 備援的新 H3 訊號，基準口數 Unit 為 `.env` 的 `H_UNIT_KEY2`。
2. 每個完整 H3 交易輪次確認平倉後，依淨損益計算連輸；第六筆虧損確認後，下一筆進場改為 `Unit + 1`，例如 Unit=3 時改成4口。此後即使繼續連輸也只多1口，直到本帳戶加碼後的已平倉累計淨損益回到前高，下一筆恢復 Unit。任何一筆淨利或淨損益為零會中斷連輸，但未回前高不解除已啟動的加口階段。
3. 每筆新H3多空訊號（含同方向）先查券商實際庫存，全平並確認空手，更新可核對的連輸與回撤帳本，再依 Unit 或 Unit+1 進訊號方向；明確空手訊號只全平。
4. 支援手動部分停利、全平或增減倉後，於新訊號同步實際剩餘口數。外部改倉導致口數不同時，該輪完整損益不明，不推估、不計輸贏；保留既有連輸／加口／累計淨損益，清除該輪換月成本，並寫入稽核紀錄。價格、浮盈、回吐都不觸發委託；加口僅影響下一筆 H3 進場的口數。
5. 舊版本停利鎖不再阻擋新的有效 H3 訊號，但重啟不補歷史訊號，也不自動重新進場。
6. 結算日保留 13:25 自動換月：核對舊月全平後，次月進相同方向、原持倉口數。換月不單獨增加連輸筆數，也不在換月中調整口數。

### 損益與啟用基準

`h3_profit_sizing` 與原持倉帳本放在同一 SQLite 資料庫，保存已平倉損益前高、累計淨損益、連輸次數、是否已加1口、完整交易筆數及本輪換月成本。平倉的持倉、損益與加口狀態在同一交易中提交；重啟沿用既存狀態，不歸零。成本模型固定為每口每次來回4.8點（估算，非券商實際費稅），與本次研究假設一致；以完整成交明細的數量加權均價計算損益，不使用報價或首筆成交價代替均價。淨損益乘以實際平倉口數，單位為點×口。

首次升級從新的損益基準0、連輸0開始，不匯入研究CSV，也不回補舊交易。若已有本策略可核對進場價的持倉，其下一次H3完整平倉為第一筆，沿用既有換月價差carry；升級前已發生的換月成本不回補。缺少原持倉成交價時仍可全平，但不推估該輪輸贏。已有未確認委託時不初始化新帳本；新帳本初始化後若內容遺失或損壞，不會自行重建歸零。

換月舊月價差累積至同一H3輪次，另累積每次換月4.8點／口的估算成本，直到最終H3出場才合併結算一筆輸贏。加口後換月仍保留原實際口數；改動 Unit 也不改既有持倉，於下一筆H3進場採用新 Unit 加上當時加口狀態。

H3 接收時間須在 30 秒內，同一標準訊號ID不重複執行；不同ID的同方向訊號也先平後開，不另以10秒同方向規則跳過。啟動從 Relay 檔尾開始，只查倉等待新訊號，不立即平倉。此服務不再依賴 MXF 一分鐘價格檔作為出場輸入。

`H_UNIT_KEY2` 必填且須為正整數，不回退使用 `H_UNIT`。修改後重啟服務才會讀取；下一筆新多空訊號先全平，再按新Unit及當時連輸加口狀態進場。

## 委託與帳本

使用帳本保存的實際 TMF 月份、市價 MKT／IOC／Auto。成交及庫存均須核對；部分成交、結果不明、庫存不符或未確認委託時保留 guard，等待核對、不重送。反向未確認平倉前不送進場單。

實盤使用 `runtime/live/api_key2.live.sqlite` 與 `records/live/`；模擬使用 `runtime/simulation/api_key2.simulation.sqlite` 與 `records/simulation/`。兩種模式不複製或共用帳本、guard 及交易紀錄。舊保護欄位保留以相容既有帳本與換月復原，不再用於價格出場。既有未確認委託仍須核對，更新不會刪除 guard 或重解讀舊委託。

與舊永豐 2 EF 共用 `ef-morning-weekend-hedge-strategy/runtime/monitor.lock`，啟動仍檢查舊 EF 帳本。一般口數不符等到新H3訊號才同步；未完成委託、查倉失敗、其他月份或同時多空庫存仍等待核對，不以淨額假設全平。

## 設定與啟動

訊號改讀 `../telegram-relay-records/h3_signal_events.jsonl`，由獨立 `h3-signal-router` 做本機優先與去重；預設每0.25秒檢查。詳見 [雙來源說明](../../docs/h3-dual-source-signals.md)。

沿用上層 `.env` 的 `API_KEY2`、`SECRET_KEY2`、`PERSON_ID2`、`CA_PATH2`、必填 `H3_PROTECTION_ACCOUNT_ID`，以及專用通知 `DISCORD_H3_TP_WEBHOOK_URL`。保留既有服務與設定名稱以免影響部署。

要啟用目前設定模式的跟單，先設定：

```dotenv
H3_PROTECTION_ENABLE_ORDERS=true
H_UNIT_KEY2=1
```

在專案根目錄檢查設定（不登入、不通知、不送單）：

```powershell
docker compose --profile h3-protection run --rm --no-deps --entrypoint python h3-profit-protection-strategy /app/backend-futures-py/h3-profit-protection-strategy/protection_monitor.py --check-config
```

啟動／套用程式更新：

```powershell
docker compose --profile h3-protection up -d --no-build --no-deps --force-recreate h3-profit-protection-strategy
docker compose logs --tail 50 -f h3-profit-protection-strategy
```

須有既存映像及運行中的 `h3-signal-router`；TG relay 保留為備援來源。一般 Compose 仍為選用 profile；Windows 一鍵啟動自 2026-10-29 起會自動選用此 profile，與 API_KEY 策略一起啟動。雙擊 `stop-windows-services.cmd` 可一併停止。啟動通知顯示「H3跟單啟動」及實際 `simulation` 模式；目前為 `simulation=False`（真實下單）。

## 離線驗證

```powershell
python -m unittest discover -s backend-futures-py/h3-profit-protection-strategy/tests -v
```

測試使用替身券商，涵蓋多空進出場、反向成交確認、價格不出場、六筆淨損後Unit+1、只加一次、回前高恢復Unit、零損益中斷連輸、完整成交加權均價、舊停利鎖相容、重啟、未知／不完整成交不重送、原子提交中斷、自動換月合併損益及實盤／模擬隔離。
