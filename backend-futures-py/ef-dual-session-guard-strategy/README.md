# EF 雙時段風控策略

這是一套與 `ef-strong-consensus-morning-flat-strategy` 並存的獨立 EF 策略服務。
它讀取既有十二套 E/F 訊號，不建立第二個 Telegram 連線，並套用兩條固定時段規則：

```text
13:44 清空全部風控部位
15:00 恢復當下最新的十二套 EF 部位

04:59 清空全部風控部位
08:45 不自動恢復；只有日盤收到新的進場或反轉訊號才重新建立該策略部位
```

預設只產生獨立 shadow 部位、績效紀錄與 Discord 通知，不會查詢券商、不會送單。
實單模式需要專屬 API 變數，不會回退使用 H3、EF strong 或 RSI 的 API key。

## 訊號與部位規則

- `raw_positions` 永遠保存十二套 EF 最新應有部位。
- `active_positions` 是套用雙時段規則後的實際策略目標。
- 13:44 只清空 `active_positions`，不破壞 `raw_positions`；15:00 以當下最新
  `raw_positions` 完整恢復。
- 04:59 同樣保留原始部位，但 08:45 不恢復。
- 04:59～08:45、13:44～15:00 收到的訊號只更新原始部位，不在休市區間建倉。
- 08:45 後的新進場或反轉訊號可以重新建立該子策略；單純出場訊號不會讓早晨已清掉
  的舊倉復活。
- 訊號採本機 `received_at`；shadow 成交採收到訊號後下一個可用分鐘的 1 分 K Open。
- 排程 shadow 價採 13:44、15:00、04:59 對應的一分 K Open；若 15:00 webhook
  缺棒，使用第一根實際可用的夜盤 K。

## 輸入與輸出

輸入：

- `../tv_doc/six_strategy_signal_events.csv`
- `../tv_doc/webhook_data_1min.csv`

獨立輸出：

- `records/ef_dual_session_position.json`
- `records/ef_dual_session_decisions.csv`
- `records/ef_dual_session_shadow_trade.csv`
- `records/ef_dual_session_order_attempts.csv`
- `records/ef_dual_session_clock_events.csv`
- `runtime/ef_dual_session_state.json`
- `runtime/ef_dual_session.lock`

`records/` 與 `runtime/` 都已加入本資料夾 `.gitignore`。

## 環境設定

沿用 `backend-futures-py/.env`：

```dotenv
DISCORD_EF_DUAL_SESSION_WEBHOOK_URL=
EF_DUAL_SESSION_POLL_SECONDS=2

# 預設必須保持 false，先跑 shadow。
EF_DUAL_SESSION_ENABLE_ORDERS=false

# 實單必須使用此策略專屬 key；不會回退到 API_KEY 或 API_KEY2。
EF_DUAL_SESSION_API_KEY=
EF_DUAL_SESSION_SECRET_KEY=

# 可省略，省略時才共用 PERSON_ID / CA_PATH。
EF_DUAL_SESSION_PERSON_ID=
EF_DUAL_SESSION_CA_PATH=

# 預設 false：即使開啟實單，啟動時也不接管既有部位；等下一筆新訊號或排程。
EF_DUAL_SESSION_RECONCILE_ON_START=false
```

實單時鐘排程不等待 K 棒檔案補齊：13:44、15:00、04:59 直接依本機
Asia/Taipei 時鐘重算目標並向專屬帳戶對帳。shadow 績效仍等待對應 K 棒後補記。
三個實單排程都只允許在排程後 10 分鐘內觸發，超過窗口不補送。
每個訊號列與每日排程都有獨立 event key，服務重啟後不會因「目標同樣是 0」而漏掉
下一天的清倉，也不會對同一事件重複送單。

實單目標另有正負 10 口硬上限；如果十二策略淨部位異常超出上限，系統會拒絕送單
並留下失敗紀錄，不會截斷成 10 口後繼續交易。

## 回測

macOS/Linux：

```bash
cd /Users/kt/Desktop/self/monitor/backend-futures-py/ef-dual-session-guard-strategy
../.venv/bin/python backtest.py \
  --start "2026-07-01 00:00:00" \
  --end "2026-08-29 00:09:00" \
  --one-way-cost-twd 24
```

Windows PowerShell：

```powershell
cd C:\path\to\monitor\backend-futures-py\ef-dual-session-guard-strategy
..\.venv\Scripts\python.exe backtest.py `
  --start "2026-07-01 00:00:00" `
  --end "2026-08-29 00:09:00" `
  --one-way-cost-twd 24
```

回測會並列原始 EF 與雙時段版本，輸出成本前損益、估計成本後損益、MDD、PF、
單邊成交口數與期末淨部位。這是短樣本研究工具，不代表未來績效。

## 測試與啟動

```bash
cd /Users/kt/Desktop/self/monitor/backend-futures-py/ef-dual-session-guard-strategy
../.venv/bin/python -m unittest discover -s tests -v
../.venv/bin/python monitor_and_trade.py
```

Docker shadow：

```bash
docker compose --profile strategies up -d --build ef-dual-session-guard-strategy
docker compose logs -f ef-dual-session-guard-strategy
```

## 上線前檢查

1. 九月保持 `EF_DUAL_SESSION_ENABLE_ORDERS=false`，與原 EF 同步跑 shadow。
2. 每天核對 13:44、15:00、04:59 三筆 clock/decision 紀錄；08:45 不應出現恢復單。
3. 核對 15:00 恢復的是「最新」原始部位，不是 13:44 舊快照。
4. 另外統計三個排程時點的實際可成交滑價。
5. 確認使用獨立永豐帳戶後，才設定專屬 API key 並開啟實單。
6. 若服務在排程時間離線，禁止直接用過期狀態補送；先人工核對券商淨部位。
