# 日線方向＋開盤區間突破回踩策略

這是均值回歸與 EF 強共識以外的獨立研究策略。核心不是在突破時追價，而是先用前一個
完成交易日的 SMA20 決定單一方向，等日盤前 30 分鐘區間被突破後，再掛回踩限價單。

資料夾責任切分與 `h3-ef-012-strategy` 相同：策略狀態機、監控程序、券商目標部位介面、
紀錄與 runtime 狀態彼此分開。預設仍是 shadow；樣本仍短，不應因為回測為正就直接放大槓桿。

## 資料夾結構

```text
opening-range-breakout-morning-flat-strategy/
├── .gitignore
├── README.md
├── auto_trade.py
├── backtest.py
├── monitor_and_trade.py
├── strategy.py
├── records/
│   └── .gitkeep
├── runtime/
│   └── .gitkeep
└── tests/
    ├── test_auto_trade.py
    ├── test_monitor_notifications.py
    └── test_strategy.py
```

- `strategy.py`：可重播的訊號、掛單、持倉與出場狀態機。
- `monitor_and_trade.py`：追蹤新 1 分 K、Discord、CSV 稽核、防重送、重啟復原與鎖檔。
- `auto_trade.py`：沿用 H3 已驗證的 TMF 目標部位對帳介面。
- `records/`：決策、影子交易、下單嘗試與最新部位快照。
- `runtime/`：狀態 JSON 與單一實例 lock；重啟時不補送歷史委託。

## 固定規則

```text
商品資料：MXF1! 1 分 K；實際下單建議先用 TMF
日線方向：前一完成交易日 Close > SMA20 只做多；Close < SMA20 只做空
開盤區間：08:45～09:14 的 High / Low
突破確認：09:15 後，收盤價第一次沿日線方向穿越開盤區間
掛單時點：本機 Record Time 後再等一個完整分鐘，避免倒填已過去成交價
回踩限價：多單＝區間高點 - 50；空單＝區間低點 + 50
限價成交：價格至少穿過限價 1 點；30 分鐘未成交就取消
停損：100 點
停利：100 點
清倉：11:00 K 的 Open
頻率：每日最多 1 筆，不做夜盤、不攤平、不加碼
```

進場 K 同時包含停利價時，不把該停利算成獲利，因為 1 分 K 無法證明先成交再碰停利；
同一根 K 同時碰停損與停利時，一律先算停損。

## 固定樣本結果

區間：2026-04-29 13:40～2026-08-27 13:44。成本每單邊 2.4 點，點值以 TMF
每點 NT$10 換算。

| 指標 | 結果 |
| --- | ---: |
| 已平倉交易 | 36 |
| 勝率 | 69.4% |
| 成本後點數 | +1,168.2 |
| 成本後 PF | 2.01 |
| 逐分鐘不利價 MDD | -450.4 點 |
| 最差單筆 | -100 點（成本後 -104.8） |
| 平均持倉 | 5.6 分鐘 |
| 08:45～11:00 曝險比 | 1.80% |

每單邊成本提高到 5 點時，成本後 +981 點、PF 1.81；提高到 10 點時，仍為
+621 點、PF 1.47。5～6 月為 +618.4 點、PF 2.18；7～8 月 27 日為
+549.8 點、PF 1.87。兩段都為正，但這不是未看過的真正樣本外測試。

研究報告與交易明細：

```text
../tv_doc/research_outputs/opening_range_break_retest_low_risk_2026-08-27.md
../tv_doc/research_outputs/opening_range_break_retest_trades_2026-08-27.csv
```

## 重跑

```powershell
cd C:\path\to\monitor\backend-futures-py\opening-range-breakout-morning-flat-strategy

python -m unittest discover -s tests -v

python backtest.py `
  --start "2026-04-29 13:40:00" `
  --end "2026-08-27 13:44:00" `
  --one-way-cost 2.4 `
  --point-value 10 `
  --trades-out ..\tv_doc\research_outputs\opening_range_break_retest_trades_2026-08-27.csv
```

## Shadow 監控

先在 `backend-futures-py/.env` 設定：

```dotenv
OPENING_RETEST_ENABLE_ORDERS=false
OPENING_RETEST_POSITION_UNIT=1
DISCORD_OPENING_RETEST=https://discord.com/api/webhooks/...
```

然後執行：

```powershell
python monitor_and_trade.py
```

規則參數都可透過以下環境變數覆寫，但 forward 測試期間應固定，不要追著近期績效調參：

```text
OPENING_RETEST_OPENING_MINUTES=30
OPENING_RETEST_DAILY_SMA_LENGTH=20
OPENING_RETEST_POINTS=50
OPENING_RETEST_EXPIRY_MINUTES=30
OPENING_RETEST_LIMIT_PENETRATION_POINTS=1
OPENING_RETEST_STOP_POINTS=100
OPENING_RETEST_TARGET_POINTS=100
OPENING_RETEST_FORCE_FLAT_TIME=11:00
OPENING_RETEST_MINIMUM_OPENING_BARS=28
OPENING_RETEST_POLL_SECONDS=2
```

`OPENING_RETEST_ENABLE_ORDERS=true` 雖已接上與 H3 相同的 TMF 目標部位介面，但它是在完整
1 分 K 確認影子限價成交後才以市價 IOC 對帳，而且不是券商原生限價＋OCO 保護單。因此目前
不符合本策略「低風險」實單條件，請保持 `false`。若日後補上券商原生限價、成交回報與保護
停損，再使用 `OPENING_RETEST_API_KEY`、`OPENING_RETEST_SECRET_KEY`；未提供時才會回退至
`API_KEY2`、`SECRET_KEY2`。

## 上線門檻

- 先固定參數做 shadow，至少新增 60 個完整交易日或 50 筆全新成交。
- 前瞻成本後 PF 至少 1.30，逐分鐘 MDD 不超過 600 點，停損滑價 95 分位不超過 10 點。
- 真實限價成交率、漏單率與停損滑價未驗證前，不接 MTX，也不放大口數。
- 實單必須使用券商端或交易所端保護停損；只靠 Python 程序輪詢不符合低風險條件。
