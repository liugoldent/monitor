# EF 強共識＋04:59 清倉策略

這是一套與 `ef-morning-flat-strategy` 相同架構的獨立策略。它讀取十二套
E/F 策略訊號，但只維護一個組合部位；不會把十二套訊號口數直接相加。

預設只記錄影子成交與發送 Discord 通知。明確設定
`EF_STRONG_MORNING_FLAT_ENABLE_ORDERS=true` 後，才使用獨立的
`API_KEY` / `SECRET_KEY` 查詢永豐 TMF 實際淨部位、送差額 IOC 市價單並回查確認。

## 規則

```text
E 組淨部位 >= +2 且 F 組淨部位 >= +2：組合多 1 口
E 組淨部位 <= -2 且 F 組淨部位 <= -2：組合空 1 口
其他情況：空手

每天 04:59 清空組合部位
08:45 不自動恢復清倉前的部位
08:45 後收到新的 E/F 訊號時，才重新計算強共識
```

E 組與 F 組各包含六套既有策略。無論同向票數多高，組合最多只有一口。

## 訊號與模擬成交

- 訊號只使用本機 `received_at`；沒有 `received_at` 的舊資料不能當成可交易事件。
- 實單模式在新訊號寫入 CSV 後立即重算完整目標、向永豐送差額單；不等下一分鐘K棒。
- 收到訊號後，使用嚴格下一分鐘的 1 分 K Open 作為 shadow 成交價。
- 例如 `08:45:04` 收到訊號，使用 `08:46` Open。
- 反向時在同一成交價先平舊方向，再開新方向。
- `04:59～08:45` 的訊號只更新原始 E/F 狀態，不建立組合部位。
- `13:45～15:00` 不清倉，部位正常延續。
- 04:59實單清倉由本機時鐘排程立即對帳，不等待K棒檔案寫入；K棒只負責稍後補記 shadow 成交價。

## EF訊號資料流

H3+EF融合服務已停用，Telegram收訊改由獨立的
`six-strategy-listener` 服務負責。它持續將有效E/F事件寫入
`tv_doc/six_strategy_signal_events.csv`；本策略和其他EF策略只讀取這份共用事件檔。
獨立收訊服務的舊版下單開關固定關閉，不會與新強共識帳號重複下單。

## 目錄與輸出

程式第一次執行時會自動建立：

```text
records/ef_strong_morning_flat_position.json
records/ef_strong_morning_flat_decisions.csv
records/ef_strong_morning_flat_shadow_trade.csv
records/ef_strong_morning_flat_order_attempts.csv
runtime/ef_strong_morning_flat_state.json
runtime/ef_strong_morning_flat.lock
```

`records/` 保存目前組合部位、每筆判斷及影子交易；`runtime/` 保存來源 CSV
讀取進度、原始十二策略部位、組合進場價及重啟狀態。
`ef_strong_morning_flat_order_attempts.csv` 是追加式實單稽核，包含開始嘗試、成功送單
並回查、帳戶已符合目標、永豐失敗與防重送略過。

影子交易的 `pnl_points` 為指數點；`pnl_twd` 預設按 TMF 每點 NT$10 計算。

## 環境設定

沿用 `../.env`：

```dotenv
# 專用 Discord webhook（未設定才回退到既有 MXF webhook）。
DISCORD_EFSTRONG_MORNING_FLAT_WEBHOOK_URL=

# 這套策略指定使用主憑證。
API_KEY=
SECRET_KEY=
PERSON_ID=
CA_PATH=

# 預設false；只有true才查實倉與下單。
EF_STRONG_MORNING_FLAT_ENABLE_ORDERS=false
EF_STRONG_MORNING_FLAT_POSITION_UNIT=1

# 研究固定值為 2；除非重新回測，不建議調整。
EF_STRONG_MORNING_FLAT_MIN_GROUP_NET=2

EF_STRONG_MORNING_FLAT_POLL_SECONDS=2
```

`EF_STRONG_MORNING_FLAT_POSITION_UNIT` 是 U，允許 1～20。強共識方向仍是
-1/0/1，永豐最終目標會縮放成 -U/0/+U；修改 U 後重啟會依新口數對帳。

## 測試

```powershell
cd C:\path\to\monitor\backend-futures-py\ef-strong-consensus-morning-flat-strategy
python -m unittest discover -s tests -v
```

## 回測

回測必須固定 `--end`，避免正式資料持續追加造成結果不可重現：

```powershell
python backtest.py `
  --start "2026-06-24 00:00:00" `
  --end "2026-08-27 16:01:00" `
  --threshold 2 `
  --one-way-cost 2 `
  --point-value 10
```

- `--point-value 10`：TMF。
- `--point-value 50`：MXF。
- `--one-way-cost` 以每次單邊調整的指數點估計，毛損益仍會分開保留。

## 啟動監控

```powershell
python monitor_and_trade.py
```

預設為 shadow。實單模式啟動時會先以當前策略目標對帳；實際帳戶
已符合目標時不送單。同一目標不論成功或失敗都不自動重送，下一次目標改變才能再委託。

## 上線門檻

目前歷史樣本仍短。建議固定規則後先累積至少 60 個全新交易日或 50 個全新已平倉腿，
再檢查扣除實際成本後的 Profit Factor、逐分鐘最大回撤及訊號漏接狀況；實單仍固定最多1口。
