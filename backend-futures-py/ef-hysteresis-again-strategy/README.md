# EF Hysteresis Again（第三策略）

這是一套獨立的前瞻影子策略。它讀取共用的
`tv_doc/six_strategy_signal_events.csv`，但擁有自己的 runtime、records、鎖檔與
Discord webhook；不連線券商，也不送出委託。

## 固定規則

- 進場門檻固定為 E、F 各自同向淨部位 2；續抱門檻固定為 1。
- 01:00 影子目標清為零；01:00～08:45 只更新原始 E/F 狀態。
- 08:45 後第一筆 EF 訊號到達時，先檢查該訊號發生前的 E/F 狀態。
- 若此前已是 `E >= +2` 且 `F >= +2`，鎖住多方，不因尾端訊號追多。
- 多方鎖定後必須先出現任一組 `< +2`；之後重新達到兩組皆 `>= +2` 才做多。
- 空方規則對稱：若此前已是 `E <= -2` 且 `F <= -2`，必須先出現任一組 `> -2`；之後重新達到兩組皆 `<= -2` 才做空。
- 啟動不回放、不補舊訊號，只從啟動後新增的 CSV 列開始處理。

Discord 使用：

```dotenv
DISCORD_EF_HYSTERESIS_AGAIN_WEBHOOK_URL=
EF_HYSTERESIS_AGAIN_POLL_SECONDS=2
```

啟動：

```powershell
docker compose up -d --build ef-hysteresis-again-strategy
```

測試：

```powershell
python -m unittest discover -s tests -v
```
