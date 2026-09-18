# 永豐策略清單

專案只保留兩條永豐策略路線：

| 帳戶 | 策略 | 目錄 | 目前狀態 |
| --- | --- | --- | --- |
| 永豐 1 | EF Hysteresis 共識＋01:00 平倉 | `ef-strong-consensus-morning-flat-strategy/`（保留舊路徑以延續 runtime） | 現行實單 |
| 永豐 2 | 純 EF＋01:00 清倉，08:45 後等新訊號 | `ef-morning-weekend-hedge-strategy/` | 預設 shadow，獨立實單開關 |

`telegram_signal_relay.py`、行情監控與 webhook 都是共用基礎設施，不是第三套策略。
H 訊號可以繼續被記錄，但不納入永豐 2 的純 EF 部位。舊 `pure-ef-hedge-strategy/` 為歷史研究規格。
