# Telegram 值班監聽器

使用既有 `.env` 內的 `TG_API_ID`、`TG_API_HASH`、`TG_SESSION` 監聽指定群組的新訊息。第一則新訊息進來後，收集其前面最多 12 則與接下來 90 秒內最多 20 則訊息，交給 Codex 判斷是否為需要排查的問題。沒有固定關鍵字。

判定為問題時，Codex 依 `boss-question-orchestrator` 的工作方式產出主管回覆草稿、事實與假設、排查分支、建議交辦及 PM 進度草稿，存成 `runtime/reports/*.report.txt`。判定不明確時存成 `*.review.txt` 供人工確認；一般聊天不存對話或報告。程式沒有 Telegram 發訊呼叫，也不會實際建立交辦任務。

圖片或檔案訊息會列入判斷，但目前不下載或讀取附件內容；只有附件而無文字時會列為待人工確認。

Codex 分析任務固定在 `/Users/kt/Desktop/work/heyu/xingba_pcweb_vue3` 執行。權限設定預設禁止讀取其他檔案，僅開放該專案與 Codex 執行工具所需的最小系統路徑；不允許命令使用網路、寫入檔案或讀取本監聽器的 `.env`。Codex 的子程序只接收必要環境變數，不會繼承 Telegram 憑證。若目標專案出現 `.codex/config.toml`，分析會拒絕執行，等待重新檢查其工具設定。

## 設定

- `CODEX_BIN`：可執行的 Codex CLI 路徑。未設定時使用 `codex`。
- `DUTY_CHAT_IDS`：必填，用逗號分隔允許監看的 chat ID。未設定時服務拒絕啟動，避免監看所有對話。
- `DUTY_CONTEXT_WAIT_SECONDS`：每批收集訊息的等待秒數，預設 90。每批都會呼叫 Codex 判斷，因此群組活躍時會產生較多分析執行。

## 啟動

一個終端同時啟動值班監聽器與前端 QA 派單監聽器：

```sh
cd /Users/kt/Desktop/self/monitor/backend-heyu-node
/Users/kt/.nvm/versions/node/v22.19.0/bin/node start-all.js
```

按 `Ctrl+C` 會一起停止。也可以使用 Node.js 22 執行 `npm start`；只啟動其中一個時使用 `npm run duty` 或 `npm run qa`。若 QA 的 launchd 背景服務已在執行，先停止它，避免同一則派單被處理兩次：

```sh
launchctl bootout gui/$(id -u)/com.kt.monitor.qa-dispatch
```

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

macOS 背景常駐可使用 `launchd/com.kt.monitor.qa-dispatch.plist`，它也會同時啟動兩個監聽器。先建立 `runtime/qa-dispatch/`，再執行 `launchctl bootstrap gui/$(id -u) launchd/com.kt.monitor.qa-dispatch.plist`；停止用 `launchctl bootout gui/$(id -u)/com.kt.monitor.qa-dispatch`。若本機路徑不同，先修改 plist 的 Node、專案和日誌路徑。
