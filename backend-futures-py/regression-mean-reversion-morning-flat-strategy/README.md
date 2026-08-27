# 回歸通道均值回歸＋13:20 清倉策略

這是與 `ef-strong-consensus-morning-flat-strategy` 相同目錄架構的獨立策略：
`strategy.py` 是共用規則與狀態機，`backtest.py` 做無偷看未來的分鐘重播，
`monitor_and_trade.py` 監控新 1 分 K，`auto_trade.py` 只使用第二帳號憑證。

預設永遠是 shadow。只有明確設定 `MEAN_REVERSION_ENABLE_ORDERS=true` 才會查詢
`API_KEY2` 帳號的 TMF 實際淨部位、送差額 IOC 市價單並回查。

## 已固定的規則

```text
商品：TMF，固定最多 1 口
中心：最近 60 根已完成 1 分 K 的最小平方法回歸線當前值
外通道：中心 ± 2.0 倍回歸殘差標準差
訊號：08:50～13:19 的收盤價位於外通道之外
成交：TradingView `Record Time` 後嚴格下一分鐘的 1 分 K Open
獲利：進場訊號當下的回歸中心（進場後凍結）
停損：進場價外側固定 100 點
清倉：13:20 K 的 Open 無條件平倉
同一方向停損一次：當天不再做該方向
每日最多進場 4 次
```

例如 08:50 K 在 08:51:04 寫入，最早只使用 08:52 Open，不會倒填已經過去的
08:51 Open。單根 K 同時碰到目標與停損時，回測採「不利的一側先發生」。區間結束若仍有部位，
以最後一根 Close 結清，避免把未實現損益藏起來。

## 趨勢與風險濾網

- 回歸斜率絕對值大於每分鐘 2.5 點：不逆勢。
- 當根真實波幅大於近期中位數 3 倍：暫停。
- 日盤跳空至少 100 點且沿跳空方向再擴張 50 點：禁止反向單。
- H 與 EF 強共識同向：禁止均值回歸做相反方向；同向回檔單仍可做。
- H/EF 互相矛盾、空手或資料未知：允許雙向，但其他濾網仍有效。

H 使用 `tv_doc/h_trade.csv`；EF 使用
`tv_doc/six_strategy_signal_events.csv` 的本機 `received_at`。沒有 `received_at` 的 EF 舊列
不會進入回測，避免用事後狀態偷看未來。現有 H 成交檔只到 2026-06-12，EF 可交易時間戳
自 2026-06-24 才開始，因此目前 H/EF 濾網證據仍不完整，這是上線前必須補足的資料缺口。

## 目前固定樣本結果

資料：`tv_doc/webhook_data_1min.csv`；成本：每次單邊調整 2 點；TMF 每點 NT$10。

| 區間 | 已平倉腿 | 毛損益 | 估計淨損益 | PF | 逐分鐘 MDD |
|---|---:|---:|---:|---:|---:|
| 2026-07-01～07-31 | 29 | +558.02 點 | +442.02 點 | 1.36 | -401.00 點 |
| 2026-08-01～08-27 13:20 | 28 | +1,034.68 點 | +922.68 點 | 1.80 | -500.00 點 |
| 合併區間 | 57 | +1,592.70 點 | +1,364.70 點 | 1.56 | -641.00 點 |

這只是短樣本研究結果，尚未包含滑價分布、委託未成交、資料漏 K 與完整 H 歷史；
`MEAN_REVERSION_ENABLE_ORDERS` 應維持 `false`，先累積至少 60 個全新交易日或 50 個全新已平倉腿。

交易明細已輸出到：

```text
tv_doc/research_outputs/regression_mean_reversion_2026_07.csv
tv_doc/research_outputs/regression_mean_reversion_2026_08.csv
```

## 環境設定

沿用上一層 `.env`，但第四帳號不回退到其他 API key：

```dotenv
DISCORD_MEAN_REVERSION=

API_KEY2=
SECRET_KEY2=
PERSON_ID=
CA_PATH=

MEAN_REVERSION_ENABLE_ORDERS=false
MEAN_REVERSION_POSITION_UNIT=1
MEAN_REVERSION_LENGTH=60
MEAN_REVERSION_CHANNEL_WIDTH=2.0
MEAN_REVERSION_STOP_POINTS=100
MEAN_REVERSION_MAX_ABS_SLOPE=2.5
MEAN_REVERSION_ABNORMAL_RANGE_MULTIPLE=3.0
MEAN_REVERSION_MIN_REWARD_RISK=1.2
MEAN_REVERSION_GAP_POINTS=100
MEAN_REVERSION_GAP_EXPANSION_POINTS=50
MEAN_REVERSION_MAX_ENTRIES_PER_DAY=4
MEAN_REVERSION_POLL_SECONDS=2
```

## 執行

```powershell
cd C:\path\to\monitor\backend-futures-py\regression-mean-reversion-morning-flat-strategy
python -m unittest discover -s tests -v

python backtest.py `
  --start "2026-07-01 00:00:00" `
  --end "2026-07-31 23:59:59" `
  --one-way-cost 2 `
  --point-value 10

python monitor_and_trade.py
```

第一次啟動監控時只從下一根新 K 開始，不會追補歷史委託。`records/` 保存判斷、影子成交
與目前部位；`runtime/` 保存重啟狀態與鎖檔。實單模式會在啟動時先對帳；同一目標無論
成功或失敗都不自動重送，必須等策略目標改變。停機期間漏掉的 K 只補算影子狀態，
不逐筆補送舊委託；補算完成後只對帳一次最終目標。

所有實單對帳嘗試都會追加到
`records/regression_mean_reversion_order_attempts.csv`，包含開始嘗試、成功送單並回查、
帳戶已符合目標、永豐失敗與防重送略過。

`MEAN_REVERSION_POSITION_UNIT` 是 U，允許 1～20。策略內部方向仍是 -1/0/1，
永豐最終目標會縮放成 -U/0/+U；修改 U 後重啟會依新口數對帳。
