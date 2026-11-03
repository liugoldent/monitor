# 浩克3V3：康和本機優先、Telegram 備援

2026-10-30 起，永豐1游擊加碼與永豐2純跟單改讀同一份標準訊號檔。

```text
康和 HISTORY（唯讀，每0.25秒查詢） ─┐
                                  ├─ h3-signal-router ─ h3_signal_events.jsonl ─ 永豐1／永豐2各自執行
Telegram relay 原始訊號紀錄 ───────┘
```

每輪先讀本機，再讀 TG；本機收到新訊號即寫檔，無須等 TG 通知。接收服務不登入券商、
不連 Telegram，也不依賴 TG relay 程序存活。兩策略各用自己的券商、口數、帳本與委託保護。
康和資料庫只以 `mode=ro` 開啟，每輪重新連線取得最新已提交資料，不修改康和檔案。

## 來源與去重

- 本機為 `C:\Concords\Concords-iQT\Dat\H124353542.tde` 的 `HISTORY` 表。
  必須以 BLOCK／SIGNAL_ID 對應 `HISTORY_SETTING.GROUP_NAME=浩克3V3`，
  ACTION 必須是「訊號部位通知（浩克3V3）」；委託與成交回報不作為訊號。
  接受 `B=1 S=0`、`B=0 S=1`、`B=0 S=0`；通知口數不決定帳戶下單口數。
- TG 沿用 `telegram_signal_events.jsonl` 中可信 `taiwan_mxf_bot` 的 H 路由 received 事件。
  保留既有多／空口數與明確空手格式；不處理 delivery／csv_record 等重複紀錄。
- 本機訊號時間採 DATE／TIME；TG 採訊息時間。兩者均須在30秒內，寫入共用檔不刷新有效期。
  來源時間倒退或相同不建立新事件；較晚的同方向通知會發布，供需先平後進的帳戶使用。既有兩個帳戶仍由各自策略忽略同方向通知。
- TG 若能對應到它之前120秒內同方向的本機通知，視為確認，不再發布。
  因此本機先多、再空之後，延遲到達的舊 TG 多單也不會把部位反轉回去。
  此為保守去重：若短時間內本機漏掉真正的新反向，且 TG 方向剛好與120秒內較早的
  本機方向相同，該 TG 也會被壓掉。TG 不含原始康和事件ID，無法完全消除此歧義。
- 本機無法讀取或沒有對應通知時，可用新的 TG 轉向備援；TG 檔故障不阻擋本機。
  本機恢復後，早於已發布訊號的舊通知只記錄、不補單。
- Router 的游標、已發布輪次及 outbox 存入自己的 SQLite；提交後斷電或寫檔中斷可復原，
  同一訊號ID不變。策略帳本也依ID去重，沿用未知委託不重送與庫存核對流程。

## 啟動與觀測

Windows 一鍵啟動／停止已加入 `h3-signal-router`；Compose 先等待它健康才啟動兩策略。
唯讀掛載整個康和 Dat 目錄，保留 SQLite journal／WAL 的正常讀取語意。
其他主機需在專案根 `.env` 設定 `CONCORD_H3_DATA_DIR`；預設為上述 Windows 目錄。

首次成功讀取來源時以檔尾／最新 rowid 建立基準，不補歷史單。兩策略啟動時同樣只接後續
新事件，既有部位與 guard 不更改；重新連線或部署期間未接到的歷史通知不追單。
Docker／電腦重啟造成掛載裝置編號改變時，只要 inode 與游標處的內容仍一致，就保留進度繼續讀取；
舊版游標第一次升級時會保留有效進度並自動補上內容指紋。
康和資料庫被替換、HISTORY 截斷或游標列內容改變時，自動以最新 rowid 重建基準，
刷新本機來源對照，但不發布既存歷史訊號。TG 檔被替換、縮短或游標前內容改變時，
同樣從目前檔尾重建基準；檔尾未完成的一行也不補發。後續新訊號恢復正常讀取。
重建基準會留下 `local_rebaseline`／`telegram_rebaseline` 稽核紀錄；
已發布訊號、outbox、最後發布方向及兩策略的帳本與 guard 均保留，不需刪除狀態。
來源暫時不存在、資料庫鎖住或格式無法讀取時仍記錄錯誤並重試，另一來源可繼續運作。

```powershell
docker compose up -d --no-build h3-signal-router
docker compose --profile h3-protection up -d --no-build --no-deps --force-recreate h3-flatten-reentry-strategy h3-profit-protection-strategy
docker compose logs --tail 50 h3-signal-router
```

新程式以 bind mount 套用；既有 `monitor-app:local` 映像含 SQLite／filelock 依賴。

紀錄皆位於 `backend-futures-py/telegram-relay-records/`：

| 檔案 | 用途 |
| --- | --- |
| `h3_signal_events.jsonl` | 兩策略共用的事件：來源時間、觀測時間、方向、來源參照及唯一ID |
| `h3_signal_sources.jsonl` | 發布、確認、重複、過期、來源故障／恢復等判定紀錄 |
| `h3_signal_router.sqlite` | 游標、來源對照及持久化 outbox |
| `h3_signal_health.json` | 最近輪詢時間、來源錯誤、游標與最後發布方向 |

Router 和兩策略預設輪詢均為0.25秒，這是檢查頻率，不是成交延遲保證。
參數為 `H3_SIGNAL_POLL_SECONDS`、`H3_REENTRY_POLL_SECONDS`、`H3_PROTECTION_POLL_SECONDS`。
健康表示程序仍在輪詢且至少一個來源可讀，不代表 TG 上游或康和伺服器一定正常。

## 測試

`test_h3_signal_router.py` 使用暫存資料庫與假券商，涵蓋本機優先、TG延遲21秒、來源故障、
資料庫鎖住、舊方向延遲抵達、反向／空手、重啟、outbox復原及雙策略各自去重。
測試不登入、不通知外部、不提交真實委託。
