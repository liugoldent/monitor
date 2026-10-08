# 浩克3先平倉再進場（API_KEY3）

讀取 Telegram Relay 的 `telegram-relay-records/telegram_signal_events.jsonl`，
只處理 `received`、H 路由、包含 `浩克3`、`訊號通知` 及
`多X口`／`空X口` 或 `(B=X S=Y)` 的新訊號（數量為整數，允許空白及換行）。
訊號辨識與可信來源規則統一由 `../h_signal.py` 提供，Relay 紀錄端與群益下單端共用，
本目錄 `strategy.py` 僅保留相容匯入，不再另外定義解析規則。
B 大於零且 S 為零判定多方；S 大於零且 B 為零判定空方。
兩邊皆為零、兩邊皆大於零或訊息方向互相矛盾時不下單。
下單端只接受 Relay 記錄的 Telegram `sender_username` 為 `taiwan_mxf_bot`
的訊號（大小寫不影響）；其他來源或缺少來源資料一律跳過。
訊息內文、顯示名稱及轉傳署名不作為來源判斷。
通知中的口數只用於辨識訊號；進場口數固定依 H_UNIT，不乘上 X。

每筆訊號都先查 API_KEY3 的實際 TMF 庫存、全部平倉，確認委託結束且庫存為零，
再獨立送出多／空 `H_UNIT` 口 TMFR1 市價 IOC 委託並確認庫存。
例如原有多3口、收到空1口、H_UNIT=1：先賣3口平倉，再賣1口進場。
以 Relay 接收時間判斷，10 秒內（含第 10 秒）連續同方向訊號只執行第一筆，
即使 Telegram message ID 不同也會過濾，記錄為 `duplicate_filtered`。
時間窗從上次接受的訊號計算，過濾訊號不延長時間窗；失敗訊號也不因重複通知重送。
超過 10 秒的新同方向訊號會先平再開；反方向訊號立即處理。
同一 Telegram chat/message ID 不重送。
其他商品不平倉；其他月份 TMF、同時多空庫存或未結束委託會停止下單，需人工核對。

設定使用 `backend-futures-py/.env`：

```dotenv
API_KEY3=帳號3的API金鑰
SECRET_KEY3=帳號3的Secret
H_UNIT=1
# 可選，未設定時沿用 PERSON_ID 與 CA_PATH
PERSON_ID3=
CA_PATH3=
# 可選 Discord；未設定仍保存本地紀錄
DISCORD_H_STRATEGY_WEBHOOK_URL=
H3_REENTRY_POLL_SECONDS=2
H3_REENTRY_RECONNECT_TIMEOUT_SECONDS=120
```

擁有獨立 runtime、records、鎖檔。啟動只查倉，跳過歷史訊號與停機期間訊號；
每次 Docker 啟動／重啟都從訊號檔案結尾開始，不依保存的訊號或部位補單，
只有服務就緒後新收到的訊號才會觸發下單。
不做每日定時清倉。斷線暫停、恢復跳過斷線期間訊號；逾時退出由 Docker 重啟。
平倉未完成絕不進新倉，失敗不自動重送；未確認委託保存在 state 的 guard，
下一筆新訊號先核對前筆委託，無法核對時停止，請先人工確認，勿直接刪狀態。

`records/entries.csv` 記錄每筆新進場的實際成交時間（台北時間）、最後成交時間、
成交口數、第一口實際成交價、委託 ID 及逐筆成交明細；Discord 同步顯示進場時間與點位。
`entry_price` 取最早成交明細的價位，不取加權均價、不乘 H_UNIT，供後續單口 MDD 研究使用。
`runtime/state.json` 的 `current_entry` 保存目前策略進場資訊，平倉確認後清除。
若券商成交明細缺漏，標記 `missing_fill_details`，時間與進場價留空，
不以送單時間或市場報價代替實際成交；需人工核對，紀錄不會自動補齊。

從 monitor 根目錄啟動（正式實單）：

```powershell
docker compose up -d --build h3-flatten-reentry-strategy
docker compose logs -f h3-flatten-reentry-strategy
python -m unittest discover -s backend-futures-py/h3-flatten-reentry-strategy/tests -v
```

雙擊根目錄 `run-windows-services.cmd` 也會啟動此服務並開啟日誌分頁；
Windows 停止服務腳本也會一起停止它。
此服務使用 live profile，未指定服務的 `docker compose up -d` 不會自動啟動它。
不修改 shioaji-demo。Relay 的 H 路由辨識同步接受浩克3與訊號通知分開出現的訊息。
