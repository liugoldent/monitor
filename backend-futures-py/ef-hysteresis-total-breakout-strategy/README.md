# EF Hysteresis TOTAL 夜盤收盤基準突破（影子策略）

獨立前瞻策略，只讀共用 EF 訊號 CSV，使用自己的 runtime、records、lock 與
Discord。它不連線券商、不載入憑證、不送委託。

## 固定規則

- 01:00 影子清倉；01:00～05:00 仍更新 E/F 原始部位，但不進場。
- 每天以 **05:00 前最後可得的 EF 狀態**（截至 04:59:59）固定 `E + F` 基準；不要求 04:59 剛好有訊號。重啟時從已保存訊號依收到時間重建這個基準。
- 05:00～08:45 不進場；08:45 後收到新的 EF 訊號才比較，不在開盤時自動補單。
- 多方須同時滿足 E ≥2、F ≥2，且更新後 TOTAL 嚴格大於 05:00 前的多方基準。
- 空方對稱：E ≤-2、F ≤-2，且空方 TOTAL 強度嚴格大於 05:00 前的空方基準。
- 進場後續抱門檻固定為1；不再依回測調整。
- 啟動不回放、不補單，只處理啟動後新增訊號。

設定：

```dotenv
DISCORD_EF_HYSTERESIS_TOTAL_BREAKOUT_WEBHOOK_URL=
EF_HYSTERESIS_TOTAL_BREAKOUT_POLL_SECONDS=2
```

啟動：

```powershell
docker compose up -d --build --no-deps ef-hysteresis-total-breakout-strategy
```
