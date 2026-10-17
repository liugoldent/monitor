# 永豐 H3 修復加碼策略（API_KEY3）

2026-10-16 起與凱基、群益直接共用 `../h3_recovery_sizing.py` 的策略狀態機。
永豐下單仍由本目錄 `auto_trade.py` 以 `API_KEY3`／`SECRET_KEY3` 登入 Shioaji 正式帳戶。

## 策略

- 第1口從啟動後的新 H3 訊號跟隨，不等待回撤。
- 首次進場那一輪維持1口，先回到單口曲線前高。
- 後續新一輪先回撤達2.5%，再於新訊號修復到≤1.25%時，進場2口。
- 回撤歸零或刷新前高後恢復1口；總口數上限2口。
- 分母為訊號當時指數；單口已平倉影子曲線、行情、歷史基準及成本假設與凱基相同。
- 各券商獨立保存1／2口階段、實際庫存、訊號去重與委託 guard；不共用實際帳戶損益。
- 只接受 Relay `received`、H 路由、可信 `taiwan_mxf_bot` 的浩克3新訊號；30秒內才有效。
- 同一 chat/message 不重送，10秒內連續同方向去重。啟動從檔尾開始，不補停機訊號。

每筆有效訊號先確認原部位完整平倉且庫存歸零，再獨立進場策略決定的1或2口。
永豐沿用 TMFR1、市價 MKT／IOC／Auto；其他商品不動，其他月份或多空並存的微台庫存會停止核對。
委託及實際庫存均須確認；整筆零成交拒單經核對後等待新訊號，不重送原單。
部分成交、逾時或結果不明保留 guard 並停止，不以刪除狀態解除阻擋。
斷線時退出，Docker 重啟後只等新訊號；啟動不送單、不自動平倉，不做每日固定清倉。

## 設定及帳本

設定來源為上層 `backend-futures-py/.env`：

```dotenv
API_KEY3=帳號3的API金鑰
SECRET_KEY3=帳號3的Secret
H_UNIT=1
# 可選：沿用 PERSON_ID／CA_PATH
PERSON_ID3=
CA_PATH3=
DISCORD_H_STRATEGY_WEBHOOK_URL=
H3_REENTRY_POLL_SECONDS=2
```

H_UNIT 必須為1；實際口數由共用策略決定，不將訊號內的X口或 H_UNIT 另乘上1／2口。
API_KEY3 少於10字元時先停止設定檢查，不向券商登入；不改用其他 API_KEY。

- `runtime/recovery.sqlite`：永豐獨立的訊號、交易 guard、1／2口階段及稽核紀錄。
- `runtime/state.json`：保留舊版狀態；若含待確認委託、未完成訊號或進場紀錄，禁止自動遷移。
- `runtime/monitor.lock`：沿用舊服務鎖，避免兩個程序同時下單。
- `records/entries.csv`：保留實際成交明細與第一口價位；缺明細不以報價冒充成交價。
- `records/live_order_attempts.csv`／SQLite audit：下單與核對紀錄。
- `../telegram-relay-runtime/h3_recovery_seed_v2.json`：共用固定單口歷史基準，Docker唯讀掛載。

## 檢查及套用

本機設定檢查（不登入、不送單）：

```powershell
python backend-futures-py/h3-flatten-reentry-strategy/monitor_and_trade.py --check-config
```

既有本機映像透過 Compose 唯讀掛載策略程式，不必重建其他服務：

```powershell
docker compose up -d --no-build --no-deps h3-flatten-reentry-strategy
docker compose logs -f h3-flatten-reentry-strategy
```

離線測試使用替身券商，不登入、不下單：

```powershell
python -m unittest discover -s backend-futures-py/h3-flatten-reentry-strategy/tests -v
python -m unittest discover -s backend-futures-py -p test_h3_recovery_sizing.py -v
```
