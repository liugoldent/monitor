# EF 凌晨與週末避險（永豐第二帳戶）

這個帳戶只對沖**群益純 EF** 的休市曝險。群益 EF 照常運作，永豐第一帳戶的強共識策略也照常運作；本策略不把第一帳戶或 H3 加入計算。

## 規則

- 每個有效夜盤的 **04:59**，讀取群益純 EF 的淨口數 `N`。
- 同日 08:45 開市：淨留最多 **2 口**。週末或連假：淨留最多 **1 口**。
- 永豐第二帳戶目標 `−sign(N) × max(abs(N) − 保留上限, 0)`。不足上限不加倉；例如平日 EF 多 7，永豐空 5；週末則空 6。EF 空 7 時方向相反。
- 鎖定本次口數，下一個實際開市日 **08:45 起** 將第二帳戶歸零；不等待新 EF 訊號。群益部位不受影響。
- 不做 13:44 避險。一般可交易時段每 5 分鐘對帳，第二帳戶目標為零，因此這必須是**專用帳戶**。
- 04:59:40 後不再補建本次避險，05:00 後不送建倉單。登入或對帳太慢，也會在實際送單前擋掉逾期委託。
- 這是降低淨口數，**不保證最大損失 3,000 點**；兩帳戶資金及保證金仍分別計算。

## 部位來源與合約

預設讀取 `../tv_doc/six_strategy_signal_events.csv`，以 `received_at` 重建 12 個 E/F 策略的最新狀態，預設每策略 1 口微台。未附時間的舊資料不參與重建；缺任何策略狀態就不建倉。會記錄前後狀態不一致次數。

**CSV 是訊號推算，不是群益券商實際庫存。** 現有資料曾出現狀態不一致；漏單、手動調倉、口數設定不同都可能使推算錯誤。本程式沒有群益查庫存接口，也無法僅靠沒有新訊號判斷 relay 是否斷線。使用推算模式實單前，必須自行核對群益純 EF 的口數與訊號接收是否正常，並設定 `EF_HEDGE_ACCEPT_SIGNAL_ESTIMATE=true`。這個開關不會把推算變成實際庫存。

也可用 `EF_HEDGE_SOURCE_SNAPSHOT=/absolute/path/source.json` 接入群益庫存快照（優先於 CSV），格式如下。由外部庫存讀取程式以原子取代方式更新；04:59 使用時必須在 120 秒內，且只包含純 EF。這裡**沒有自動產生該快照的群益連線程式**。

```json
{
  "source": "capital_pure_ef",
  "observed_at": "2026-09-15T04:58:50+08:00",
  "net_position": 7,
  "contract": "請填券商的實際 TMF 月份合約代碼"
}
```

CSV 模式使用 `EF_HEDGE_CONTRACT` 指定**與群益相同月份**的券商實際 TMF 合約代碼，不能填 `TMFR1`/`TMFR2`。程式不自動換月；若群益換月，必須同步更新設定並重啟。已開立避險會使用狀態中保存的原合約解除。第二帳戶若已有其他月份 TMF，會停止並通知，避免把跨月部位當成同一部位。

## 設定與啟動

沿用 `backend-futures-py/.env`，環境變數優先。不要將真實金鑰寫入版本控制。

```dotenv
API_KEY2=第二組金鑰
SECRET_KEY2=第二組密鑰
PERSON_ID=憑證身分證字號
CA_PATH=/absolute/path/Sinopac.pfx
DISCORD_EF_hedge_WEBHOOK_URL=專用DiscordWebhook

EF_HEDGE_ENABLE_ORDERS=false
EF_HEDGE_ACCOUNT_ID=API_KEY2所屬的期貨account_id
EF_HEDGE_CONTRACT=與群益相同月份的實際TMF合約代碼
EF_HEDGE_ACCEPT_SIGNAL_ESTIMATE=false
EF_HEDGE_SOURCE_UNIT=1
EF_HEDGE_WEEKDAY_CAP=2
EF_HEDGE_HOLIDAY_CAP=1
EF_HEDGE_MAX_CONTRACTS=12
```

如果第二組使用不同憑證，可以設定 `PERSON_ID2` / `CA_PATH2`；金鑰只讀 `API_KEY2` / `SECRET_KEY2`，**不會退回第一組**。登入後核對 `EF_HEDGE_ACCOUNT_ID`。Discord 只使用大小寫完全相同的 `DISCORD_EF_hedge_WEBHOOK_URL`，不會改送別的策略 webhook。

```bash
cd backend-futures-py/ef-morning-weekend-hedge-strategy
python monitor_and_trade.py --once
python monitor_and_trade.py
```

預設 `shadow` 只記錄目標與口數，**不宣稱模擬成交價格或損益**。完成來源、帳號、月份與日曆設定後，`EF_HEDGE_ENABLE_ORDERS=true` 才會下實單；`--once` 也遵守這個實單開關。需先安裝上層 `requirements.txt`。

## 日曆、重啟與委託

`config/calendar.json` 提供 2026 年預定休市日，參考[期交所行事曆](https://www.taifex.com.tw/file/taifex/CHINESE/4/2026Calendar.pdf)與[證交所日期表](https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=html)。例如 9/25 04:59 避險，9/29 08:45 解除。日曆之外的日期停止判斷，不猜測次年開市日。

臨時休市須更新 `closed_dates`；特殊開市用 `open_dates`；若某日取消下午開始的夜盤，用 `no_night_dates`。每秒重新載入日曆，也可用 `EF_HEDGE_CALENDAR_PATH` 指定日曆。**未串接臨時休市公告**，長假或颱風前須核對最新公告。跨入 2027 前須更新涵蓋範圍與假日。

`runtime/monitor.lock` 防止同資料夾多開；live/shadow 狀態與記錄分開。每筆委託前先原子保存意圖，下單用市價 IOC，並驗證券商實際部位。成功後重啟不再重送同一動作。失敗、部分成交或程式在下單中斷，都不盲目重試；下次開市會先核對未結束委託，再嘗試將避險部位歸零。若解除委託本身失敗，會鎖定，不會每秒重送。

遇到失敗先查看券商庫存及委託狀態，停掉原監控，再用 `python monitor_and_trade.py --retry-failed` 解除該動作鎖定。即使解除鎖定，執行器仍會檢查未結束委託；錯過建倉期限也不補建。不要刪除 live 狀態或同時從別台主機跑第二份。

記錄在 `records/{live,shadow}_events.csv`，包含來源快照、上限、口數、合約、意圖與結果。網路通知在背景執行，避免卡住 04:59。通知失敗會印出本機訊息，不重送；請以本機紀錄與券商庫存為準。

## 驗證與離線回放

```bash
python -m unittest discover -s tests -v
python backtest.py --start '2026-08-26 04:59:00' --end '2026-09-14 11:05:00'
```

回放只計算**第二帳戶的避險損益**，不是群益與第二帳戶合計；使用 MXF1! 的精確 04:59 / 開市 08:45 Open 作代理，微台每點每口 10 元，預設單邊成本 2 點。缺少邊界 K 或完整 EF 狀態會列入 `skipped`，不跨缺口找下一根冒充開盤。回放與實單都用 04:59 當下已收到的訊號，實單可能有滑價。既有研究的下一分鐘成交口徑與本回放的訊號狀態口徑可能不同，不能直接當成相同報酬序列。
