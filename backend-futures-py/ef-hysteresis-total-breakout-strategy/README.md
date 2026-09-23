# EF Hysteresis TOTAL 突破（影子策略）

獨立前瞻策略，只讀共用 EF 訊號 CSV，使用自己的 runtime、records、lock 與
Discord。它不連線券商、不載入憑證、不送委託。

## 固定規則

- 01:00 影子清倉；01:00～08:45 仍更新 E/F 原始部位。
- 08:45 後第一筆 EF 訊號發生前，將當時 `E + F` 固定為本交易週期基準。
- 多方須同時滿足 E ≥2、F ≥2，且更新後 TOTAL 嚴格大於多方基準。
- 空方對稱：E ≤-2、F ≤-2，且空方 TOTAL 強度嚴格大於空方基準。
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
