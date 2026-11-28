# H2／H3 固定口數跟單（API_KEY）

本策略使用永豐正式帳戶 `API_KEY`／`SECRET_KEY`（`simulation=False`），初次建立帳本時以微台近一 `TMFR1` 解析實際月份，之後固定使用帳本的實際合約下單。

庫存、帳本與委託使用同一個實際合約代碼（例如 `TMFJ6`）。結算日自動換月後使用次月實際合約，無須等待 `TMFR1` 別名切換。缺少實際合約代碼或非換月流程中持有其他月份時，服務等待核對。復原時唯讀核對原委託編號、商品、方向、口數及目前庫存。完整成交經備份後入帳。整筆零成交終止且庫存仍與送單前相符時，原子記錄結果並解除委託阻擋，等待新訊號。部分成交或庫存與原委託結果不符時維持阻擋。
Compose 沿用服務名 `h3-flatten-reentry-strategy`，命令已切換至本目錄。
舊修復加碼策略程式及帳本保留；新策略與舊策略共用版本切換執行鎖，避免兩個版本並行。
2026-10-18 依使用者要求由第三帳戶改用 `API_KEY`，登入只讀取主帳戶的金鑰、憑證與身分設定。
使用新的 `runtime/api_key.sqlite` 及 `records/api_key/`；原第三帳戶 `runtime/state.sqlite`
與紀錄保留、不載入新帳戶。新帳戶已有策略月份微台庫存時，等待新訊號再計算差額。

2026-10-18 起，本策略為唯一預設啟動的交易策略。Compose 與 Windows 一鍵啟動均保留
Relay、MXF、Webhook、Cloudflare 與本策略共 5 個服務；EF、群益、凱基不自動啟動。

## API_KEY 同帳號 H3／H2

H2 與 H3 各固定 1 口。H3 收到空手訊號時，H3 目標歸零。

- H3 沿用原雙訊號源及訊號時效。H2 每 0.25 秒優先唯讀查詢
  Concords 的「浩克2V3」部位通知，並以可信 Telegram 發送者的通知備援。獨立的 H2
  router 會比對本機訊號以排除重複轉送，啟動時只建立游標。只有 B=1 S=0 與 B=0 S=1 更新 H2 方向。
- H2 原則上要求 Telegram 訊息在 30 秒內。若調整只減少帳戶淨部位且不反向，訊息上限放寬至 45 秒。
  兩種情況都要求轉送服務記錄的收件時間在 30 秒內，下單前仍重新檢查。
- 每個有效、未重複的訊號先取消同帳號、同策略月份的微台掛單，含手動掛單，
  確認結束後再讀取同月券商實際庫存。每筆訊號更新對應策略的目標，
  委託口數按「H3 目標＋H2 目標－券商實際淨部位」計算。差額為零時只撤掛單。
- 首次啟用將舊策略帳本部位歸給 H3，H2 從零開始。啟動不補舊訊號。
  兩套策略部位持久化於原 SQLite。啟動或重連查到同月庫存與策略目標不同時，
  記錄差異並等待新訊號。未確認委託、部分成交或其他月份庫存仍須核對。
  淨部位為零時仍保留兩套策略帳本；結算換月只搬移實際淨部位。
舊帳本的部位沿用至對應策略的下一筆有效訊號。券商差額單與策略部位各自記錄。

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

換月後進出場使用保存的次月實際合約。新 H3 訊號採固定口數。
換月延續既有部位。

`h3_auto_rollover` 保存階段（close/open/done）；每腿有唯一 intent_id、券商 trade_id 及持久化 guard。
每日重啟不重做已完成換月；舊月已平、次月尚未下單時可接續下一腿。
若成交後、寫入策略帳本前斷線，必須唯讀核對原委託與庫存後才解除 guard，不重送已成交單。
部分成交、拒單、結果不明或缺少唯一委託會保留保護並通知，絕不盲目補單或直接開下一腿。
13:30後不再啟動兩腿的新委託；錯過窗口時通知人工核對，已成交的原委託仍可事後核對入帳。
換月期間收到的 H3 訊號保留游標，完成後仍須通過原有30秒時效，不追過期單。
本流程內建於交易服務，不另建立排程器；電腦、Docker及該服務必須在13:25運行。

`rollover.ManualRollover` 僅保留舊的手動帳本銜接工具與測試；正式服務使用 `AutomaticRollover`，
  結算日換月按既有流程處理。一般交易日的手動部位差異在下次有效訊號時計算。

- 委託使用既有 Shioaji MKT／IOC／Auto，送單前持久化 guard，完整成交且庫存一致後才更新策略帳本。
  拒單、部分成交或結果不明時會保留核對紀錄，服務持續重試核對，不自動重送。

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

Relay 的 H3 附註列出兩個帳戶的固定口數規則。
Relay 啟動通知只確認轉送服務已啟動，不表示 API_KEY 交易策略正在運作或已成交。
交易策略的啟動通知會顯示核對後的 H3、H2 與帳戶淨部位。

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

本策略所有 Webhook 通知（啟動、啟動／每日查倉、進場、平倉、缺少成交價及錯誤復原）
統一使用 `../.env` 的 `DISCORD_H3_GUERRILLA_WEBHOOK_URL`，不回退至舊的通知網址。

## 帳本與測試

自動換月或其他帳本異常仍停在核對階段，每 60 秒重新核對；
相同錯誤的應用程式日誌與通知每 5 分鐘提醒一次。
`pending=False` 僅表示沒有未確認委託，不代表空手或正常跟單。
一般交易日手動改倉後，策略帳本須先與券商庫存核對。

- `runtime/api_key.sqlite` 保存 API_KEY 策略部位、成交成本、回撤帳本、訊號去重與稽核。
- `../h3-flatten-reentry-strategy/runtime/monitor.lock`：版本切換鎖，防止新舊策略並行。
- `records/api_key/entries.csv`／`records/api_key/live_order_attempts.csv`：API_KEY 實際成交與委託紀錄。
- `records/api_key/notifications.jsonl`：沿用既有 Discord 通知與發送稽核。

離線測試使用替身券商，不登入、不送實單：

```powershell
python -m unittest discover -s backend-futures-py/h3-follow-add-strategy/tests -v
docker compose config --quiet
```
