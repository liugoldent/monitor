# Telegram 值班監聽器

使用既有 `.env` 內的 `TG_API_ID`、`TG_API_HASH`、`TG_SESSION` 監聽指定群組的新訊息。第一則新訊息進來後，收集其前面最多 12 則與接下來 90 秒內最多 20 則訊息，交給 Codex 判斷是否為需要排查的問題。沒有固定關鍵字。

判定為問題後分兩條路：資訊不足、平台不明或需外部系統證據的「通靈」問題走 `ORCHESTRATE`，依完整 `boss-question-orchestrator` 技能產出拆解與回覆草稿；唯讀查證兩個專案後，有具體程式證據、預期行為與驗證方案的問題走 `FIX_PC` 或 `FIX_MOBILE`。問題分析存成 `runtime/reports/*.report.txt`；不明確時存成 `*.review.txt`，一般聊天不存報告。沒有 Telegram 發訊呼叫，也不會實際建立交辦任務。

自動修正使用 `xingba_pcweb_vue3` 或 `xingba_mobileweb_vue3_rewrite` 目前 HEAD 建立獨立 `codex/duty-*` 分支及 `runtime/reports/<批次>.worktree`，不含原 checkout 未提交內容。修正工作依序執行，遵守專案規則，完成後保留 diff 供檢查；不提交、push、建立 MR、合併或部署。修正結果存成 `*.repair.txt`，狀態及 worktree 路徑存成 `*.repair-job.json`，執行日誌存成 `*.repair-log.txt`。缺證據或必要驗證未完成時回報 `NEEDS_INPUT`/`FAILED`，並列出問題拆解；網路停用，依賴未安裝或需線上驗證可能因此受阻。修正程序最多執行 30 分鐘。

圖片訊息會下載到 `runtime/reports/<批次>.images/`，連同前後文字透過 Codex CLI 的 `--image` 交給模型判讀；支援 Telegram 照片及以檔案傳送的 JPEG、PNG、WebP。每批最多 8 張、每張最多 10 MiB。終端顯示圖片來源訊息 ID 與本機路徑；下載失敗、超出限制、其他檔案或圖片不清楚時，會明確標記資訊缺口，文字足以判定問題時仍可產出問題報告。圖片會留在本機供查核，並隨文字交給 Codex 模型處理。分析權限僅額外開放本批成功下載的圖片檔案。

Codex 分析任務固定在 `/Users/kt/Desktop/work/heyu/xingba_pcweb_vue3` 執行。權限設定預設禁止讀取其他檔案，僅開放該專案與 Codex 執行工具所需的最小系統路徑；不允許命令使用網路、寫入檔案或讀取本監聽器的 `.env`。Codex 的子程序只接收必要環境變數，不會繼承 Telegram 憑證。若目標專案出現 `.codex/config.toml`，分析會拒絕執行，等待重新檢查其工具設定。

## 設定

- `CODEX_BIN`：可執行的 Codex CLI 路徑。未設定時使用 `codex`。
- `DUTY_CHAT_IDS`：必填，用逗號分隔允許監看的 chat ID。未設定時服務拒絕啟動，避免監看所有對話。
- `DUTY_CONTEXT_WAIT_SECONDS`：每批收集訊息的等待秒數，預設 90。每批都會呼叫 Codex 判斷，因此群組活躍時會產生較多分析執行。

## 啟動

一個終端同時啟動值班監聽器、前端 QA 派單監聽器與平台前端 MR 審查監聽器：

```sh
cd /Users/kt/Desktop/self/monitor/backend-heyu-node
/Users/kt/.nvm/versions/node/v22.19.0/bin/node start-all.js
```

按 `Ctrl+C` 會一起停止。也可以使用 Node.js 22 執行 `npm start`；只啟動其中一個時使用 `npm run duty`、`npm run qa` 或 `npm run mr`。服務只在手動執行的終端機中運作，終端機關閉後即停止。`start-all.js` 會用 `runtime/watcher.pid` 拒絕第二份程序，避免重複監聽。

值班監聽器會將指定群組收到的每則新訊息印在終端機，以 `[duty] received event` 開頭，包含接收與發送時間（UTC）、群組 ID、訊息 ID、發話者 ID、附件類型與完整文字。`decision=accepted` 表示進入批次處理；`ignored_outgoing`、`ignored_unsupported_content`、`ignored_duplicate` 表示略過原因。也會顯示等待收集、併入現有批次與分析結果，方便分辨是否收到及是否執行判讀。圖片僅顯示附件類型。修改後須重新啟動 `start-all.js` 才會生效。

不知道群組 ID 時，執行 `node list-groups.js` 列出所有群組，或執行 `node list-groups.js QA` 依名稱搜尋。把想監看的 ID 設為 `.env` 內的 `DUTY_CHAT_IDS=-1001234567890`；多個群組以逗號分隔。修改 `.env` 後需重新啟動監聽器才會生效。

需要 Node.js 18 以上和已登入的 Codex CLI。本機請使用 Node.js 22 啟動。程式只在本機產出報告；目前不向 Telegram、Discord 或其他聊天室傳送訊息。`runtime/` 內可能包含工作對話，已排除 Git 追蹤。

啟動機器必須能直接讀取上述專案路徑；路徑不存在或變成符號連結時，監聽器會拒絕啟動。目前 `docker-compose.yml` 沒有掛載該專案與 Codex CLI，因此要使用這個受限分析流程，請在本機啟動 `backend-heyu-node`。

如果 `.env` 的 Telegram session 已失效，在本機終端機執行 `node reauthorize.js`，依提示輸入手機號碼、驗證碼和可能的兩步驗證密碼。驗證完成後會更新本機 `.env`，不會傳送聊天訊息。

## 前端 QA 派單監聽

另一個獨立程序 `npm run qa` 會監聽且只監聽 `前端QA提測裙` 的新訊息。訊息同時有 `【派單】`、`工單：#` 後接五碼數字，以及 `@Anforderungsfluss` 才進入處理。它先登入並確認該工單頁可讀；VPN、DNS、登入或權限有問題時不啟動 Codex。工單號去重狀態與執行紀錄放在忽略追蹤的 `runtime/qa-dispatch/`。

在本機 `backend-heyu-node/.env` 增加：

```dotenv
QA_CHAT_ID=前端QA提測裙的數字群組ID
QA_ISSUE_USER=工單網站帳號
QA_ISSUE_PASSWORD=工單網站密碼
QA_PROJECT_DIR=/Users/kt/Desktop/work/heyu/xingba_pcweb_vue3
# QA_NOTIFY_CHAT_ID=要接收完成通知的 Telegram 聊天 ID；預設發到自己的 Saved Messages
```

用 `npm run groups -- QA` 查群組 ID。執行 `npm run qa` 前，確認 VPN、Codex CLI 登入及 Xingba 專案可用。Codex 收到指令後會從 `dev` 建立 `fix/五碼工單號` 的 Worktree，實作、驗證、建立 MR，成功後清理 Worktree。收到的群組訊息不會直接成為 Codex 指令。程式只會在任務結束時送 Telegram 通知；若網站無法讀取，僅在本機日誌記錄並停止該次派單。

## 平台前端 MR 審查

`npm run mr` 只監聽名稱完全符合 `平台前端` 的群組。收到 `@Anforderungsfluss 累了嗎，放下手邊工作，放個一天假，明天再看 MR 吧！` 時，讀取它前一則訊息。前一則必須同時有 `mr:pc` 和一個 `xingba_pcweb_vue3` 的 MR 網址。訊息是觸發資料，不直接成為 Codex 指令。

除了即時事件，MR 監聽器啟動時及每 30 秒也會讀取最近 100 則訊息，回補最近四小時內漏接的觸發訊息。已排入佇列或已完成的觸發訊息不會重複審查。修改程式後須重新啟動 `start-all.js` 才會生效。

監聽器對「平台前端」每則首次看到的訊息，會在終端輸出 `received event`（即時更新）或 `received history`（歷史回補）、訊息 ID、處理判斷與文字預覽。完整文字及判斷以 JSON Lines 記在忽略 Git 追蹤的 `runtime/mr-review/received.jsonl`；同一訊息不重複記錄。`trigger_queued` 表示已排入審查，`already_access_blocked` 表示先前已收到但網頁存取受阻，`ignored_non_trigger` 表示收到但不是指定觸發句。

程式先在 12 秒內確認 MR 頁面能開啟且可讀；連線逾時、VPN 未開、登入或權限不足時，記錄原因、發送停止通知並結束本次審查。可讀時由 Codex 檢查 MR 差異、pipeline、討論、衝突及核准狀態，產出通過、不通過或資訊不足的繁體中文報告。報告存於 `runtime/mr-review/`，並以 Telegram 檔案傳給自己的 Saved Messages；同一觸發訊息不重複審查。

MR 頁面可讀後，程式會從 MR 標題擷取唯一的五碼工單號，使用既有 `QA_ISSUE_USER`、`QA_ISSUE_PASSWORD`（或專用的 `MR_ISSUE_USER`、`MR_ISSUE_PASSWORD`）登入 `https://nvshenn.bar/issues/xxxxx`，讀取需求描述，再請 Codex 逐條對照 MR。若標題沒有唯一五碼號、工單網站因 VPN／登入／逾時而不可讀、或需求描述無法擷取，該次審查停止並傳 Telegram 通知至 `MR_NOTIFY_CHAT_ID`；未設定時沿用 `QA_NOTIFY_CHAT_ID`，再未設定則送 Saved Messages。停止原因與通知狀態會寫入 `runtime/mr-review/state.json`，未成功通知的停止紀錄會在重啟時重試。

MR 頁面檢查使用 Node.js 的 HTTP 請求，不共用 Chrome `keedem.l` 等瀏覽器設定檔的登入狀態。即使 `git ls-remote` 可讀取 MR ref，只要網頁導向登入頁，審查仍會停止並通知；同一觸發訊息重新啟動後不會重試。恢復存取後需在群組傳送新的觸發訊息。

可在 `.env` 選填 `MR_CHAT_ID`（平台前端群組 ID）、`MR_NOTIFY_CHAT_ID`（接收報告的聊天 ID）、`MR_PROJECT_DIR`（本機 Xingba 專案路徑）。未指定群組 ID 時，程式依群組名稱尋找；通知 ID 預設沿用 `QA_NOTIFY_CHAT_ID`，若也未設定則送至 Saved Messages。
