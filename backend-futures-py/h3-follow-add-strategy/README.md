# H3 連輸六筆加口（API_KEY）

本策略使用永豐正式帳戶 `API_KEY`／`SECRET_KEY`（`simulation=False`），初次建立帳本時以微台近一 `TMFR1` 解析實際月份，之後固定使用帳本的實際合約下單。

庫存、帳本與委託使用同一個實際合約代碼（例如 `TMFJ6`）。結算日自動換月後使用次月實際合約，無須等待 `TMFR1` 別名切換。缺少實際合約代碼或非換月流程中持有其他月份時，服務等待核對。`reconcile.reconcile_filled_order` 只能補記已完整成交的原進場、加碼或平倉：必須核對策略 guard、券商 guard（或已核對紀錄）、唯一委託編號、商品、方向、口數、進場／加碼成交明細及目前庫存，備份 SQLite 後才原子更新帳本並解除 guard；此函式不送單。
Compose 沿用服務名 `h3-flatten-reentry-strategy`，命令已切換至本目錄。
舊修復加碼策略程式及帳本保留；新策略與舊策略共用版本切換執行鎖，避免兩個版本並行。
2026-10-18 依使用者要求由第三帳戶改用 `API_KEY`，登入只讀取主帳戶的金鑰、憑證與身分設定。
使用新的 `runtime/api_key.sqlite` 及 `records/api_key/`；原第三帳戶 `runtime/state.sqlite`
與紀錄保留、不載入新帳戶。新帳戶若有帳本以外的既有微台庫存，會持續等待核對，不直接接管。

2026-10-18 起，本策略為唯一預設啟動的交易策略。Compose 與 Windows 一鍵啟動均保留
Relay、MXF、Webhook、Cloudflare 與本策略共 5 個服務；EF、群益、凱基不自動啟動。

## 交易規則（2026-11-04更新）

- 已移除浮盈500點＋游擊穿越的盤中加碼；不再輪詢股威資料。
- 使用 `mdd_runtime.MddEngine`，基準口數為 `H_UNIT=2`。新H3訊號先依券商實際庫存全平，確認後再進場；空手訊號只全平。同ID去重，異ID同方向仍先平後開。
- 依 API_KEY 的完整成交輪次計算單口毛點數，連輸計數自切換起算。手動改倉造成成本不完整的輪次排除。
- 連續六筆平倉虧損後，下一筆進場為 **Unit+1（3 口）**。累計毛點數回到前高後恢復 **Unit（2 口）**。
- `h3_mdd_sizing` 保留券商成交核對用途，與部位、guard在同一SQLite交易更新。完整成交價缺漏時保留guard，唯讀核對原單後才入帳，不重送。
- 換月維持原口數，券商帳本保留舊月成交供核對。
- 訊號來源為 `../telegram-relay-records/h3_signal_events.jsonl`，由獨立router提供康和優先／可信TG備援訊號，30秒內有效。啟動不補歷史、不立即調整舊倉。

## 結算日 13:25 自動換月

2026-10-21 依使用者明確授權，改為程式自動轉倉，無須等待手動操作。
H3 服務須於結算日 13:25 前運行並保持券商連線；到時由監控迴圈的首次輪詢開始。
日期列在 `auto_rollover.SETTLEMENT_DATES`，涵蓋使用者指定的 2026/10 至 2027/12 共15個月，
並逐月與券商合約 `delivery_date` 核對，包括 2027/09/16；不是自行推算第三個星期三。
日期衝突、缺少合約、庫存與帳本不一致或有未確認委託時，停止送單並通知。

1. 備份 SQLite，持久化舊月份、次月、原方向及口數的換月計畫。
2. 舊月持有部位時，先用舊月實際合約全平；核對整筆成交與實際空手後才提交下一階段。
3. 以次月實際合約進相同方向、相同口數；核對完整成交、成交價與庫存後完成換月。
4. 原本空手時只切換帳本月份，不下單；之後等待新 H3 訊號。

換月後進出場使用保存的次月實際合約；新H3進場依連輸帳本採Unit或Unit+1。
換月本身維持原口數，不觸發額外加口。

`h3_auto_rollover` 保存階段（close/open/done）；每腿有唯一 intent_id、券商 trade_id 及持久化 guard。
每日重啟不重做已完成換月；舊月已平、次月尚未下單時可接續下一腿。
若成交後、寫入策略帳本前斷線，必須唯讀核對原委託與庫存後才解除 guard，不重送已成交單。
部分成交、拒單、結果不明或缺少唯一委託會保留保護並通知，絕不盲目補單或直接開下一腿。
13:30後不再啟動兩腿的新委託；錯過窗口時通知人工核對，已成交的原委託仍可事後核對入帳。
換月期間收到的 H3 訊號保留游標，完成後仍須通過原有30秒時效，不追過期單。
本流程內建於交易服務，不另建立排程器；電腦、Docker及該服務必須在13:25運行。

`rollover.ManualRollover` 僅保留舊的手動帳本銜接工具與測試；正式服務使用 `AutomaticRollover`，
不再等待手動轉倉，也不在一般交易日任意接管帳本外的手動部位。

- 委託使用既有 Shioaji MKT／IOC／Auto，送單前持久化 guard，完整成交且庫存一致後才更新策略帳本。
  拒單、部分成交、結果不明或人工改倉會保留核對紀錄，服務持續重試核對，不自動重送。
- 舊 H3 帳本有部位或未確認委託仍阻擋啟動。新策略帳本與券商口數不同時，等待新H3訊號，再同步實際口數全平；同步前不加碼。未完成委託、查倉失敗、其他月份或同時多空庫存仍等待核對，不以淨額假設全平。
  啟動時須先停止舊程序並核對其庫存／未完成委託。

## 服務持續運作與復原

執行期錯誤不再讓主程序退出。服務預設每 5 秒重新建立券商連線並唯讀核對；
`H3_RECOVERY_RETRY_SECONDS` 可調為 1～60 秒。失敗登入及已關閉的連線會清除快取，
避免重用已登出的 session。通知改為「復原中」，相同錯誤每 5 分鐘至多通知一次。
一般訊號核對成功後恢復監控新訊號，不補送錯過的訊號，也不自動接續反向進場。
自動換月另依已持久化的 close/open 進度接續，詳見上節。
部分成交、缺少唯一委託、庫存不符等情況會持續等待核對，這時服務仍在，但不送新委託。
Compose 本服務採 `restart: always`，主程序意外退出時由 Docker 重啟；
重啟仍保留 guard、帳本與去重紀錄。手動停止後須重新啟動，設定變更須重新建立容器才生效。

## 啟動與每日 08:35 查倉

Relay 的 H3 附註只說明游擊策略規則及已接收訊號，不再附加舊的 2.5%→1.25% 回撤提示。
Relay 啟動通知只確認轉送服務已啟動，不表示 API_KEY 交易策略正在運作或已成交。
交易策略自己的啟動通知會顯示核對後的保存部位與本輪是否已加碼；不因重啟重置為1口。

每次啟動會讀取 API_KEY 帳戶的微台庫存，並將多／空口數、策略帳本口數與是否一致
寫入日誌及既有 Discord 通知。每日台北時間 08:35 起查詢一次，當日稍晚才啟動時
以啟動查倉作為當天查詢。程序不中斷時由監控迴圈於 08:35 後的第一次輪詢執行。

當日已嘗試查詢的紀錄保存在 `runtime/api_key.sqlite` 的 `h3_inventory_reports`，
重啟不重複發送當天定時查倉；每次啟動仍會另外發送啟動查倉。
查詢失敗時通知失敗，不推定空手、不自動重試當天定時查詢。
此功能僅查詢與通知，不進場、不平倉、不撤單，也不自動修正庫存。
正常下單前仍沿用原有庫存核對與委託保護。

## 設定與啟動

設定沿用 `../.env`，不要將金鑰提交至 Git：

```dotenv
API_KEY=有效的交易帳戶API金鑰
SECRET_KEY=同組交易帳戶Secret
H_UNIT=2
PERSON_ID=交易帳戶身分設定
# 留空時使用 backend-futures-py/Sinopac.pfx
CA_PATH=
DISCORD_H3_GUERRILLA_WEBHOOK_URL=
H3_REENTRY_POLL_SECONDS=2
H3_GUERRILLA_POLL_SECONDS=30
```

先在專案根目錄執行本機檢查（不登入、不下單）：

```powershell
python backend-futures-py/h3-follow-add-strategy/monitor_and_trade.py --check-config
```

確認有效金鑰、Docker 引擎運行，且已核對舊 H3 部位後，套用既有帳戶服務：

```powershell
docker compose up -d --no-build --no-deps h3-flatten-reentry-strategy
docker compose logs -f h3-flatten-reentry-strategy
```

需有既存 `monitor-app:local` 映像，及運行中的 Relay／webhook 行情服務。
`monitor-mxf` 也新增共用 reader 的掛載；如使用現有映像，需在下次套用其程式變更時
`docker compose up -d --no-build --no-deps monitor-mxf`，確保新 reader 可被載入。

本機設定檢查不登入券商、不驗證金鑰是否獲券商接受，也不送單。
第三帳戶的 API_KEY3／SECRET_KEY3／PERSON_ID3／CA_PATH3 不作為本策略的備援設定。

本策略所有 Webhook 通知（啟動、啟動／每日查倉、進場、加碼、平倉、缺少成交價及錯誤復原）
統一使用 `../.env` 的 `DISCORD_H3_GUERRILLA_WEBHOOK_URL`，不回退至舊的通知網址。

## 帳本與測試

自動換月或其他帳本異常仍停在核對階段，每 60 秒重新核對；
相同錯誤的應用程式日誌與通知每 5 分鐘提醒一次。
`pending=False` 僅表示沒有未確認委託，不代表空手或正常跟單。
一般交易日手動部位只在新H3訊號到來時同步全平，不在啟動或游擊觀測時自行改倉。

- `runtime/api_key.sqlite`：API_KEY 策略部位、成交成本、連輸加口帳本、訊號去重與稽核；舊觀測欄位保留相容。
- `../h3-flatten-reentry-strategy/runtime/monitor.lock`：版本切換鎖，防止新舊策略並行。
- `records/api_key/entries.csv`／`records/api_key/live_order_attempts.csv`：API_KEY 實際成交與委託紀錄。
- `records/api_key/notifications.jsonl`：沿用既有 Discord 通知與發送稽核。

離線測試使用替身券商，不登入、不送實單：

```powershell
python -m unittest discover -s backend-futures-py/h3-follow-add-strategy/tests -v
docker compose config --quiet
```
