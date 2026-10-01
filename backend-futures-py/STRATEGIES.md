# 永豐策略清單

目前 Again 與純 EF 兩套運行策略都使用 Shioaji 模擬帳戶；舊 Hysteresis 與 TOTAL Breakout 預設不啟動：

| 帳戶 | 策略 | 目錄 | 目前狀態 |
| --- | --- | --- | --- |
| 永豐 1 | EF Hysteresis Again（含持多時 0→-1 平多） | `ef-hysteresis-again-strategy/` | API_KEY，Shioaji `simulation=True` |
| 模擬帳戶 | 舊 EF Hysteresis 共識＋01:00 平倉 | `ef-strong-consensus-morning-flat-strategy/` | 同組 API_KEY 憑證，Shioaji `simulation=True` |
| 永豐 2 | 純 EF 合計方向＋多空對稱否決、01:00 清倉 | `ef-morning-weekend-hedge-strategy/` | API_KEY2，Shioaji `simulation=True` |
| 不連券商 | EF Hysteresis TOTAL 突破（超越08:45前總部位） | `ef-hysteresis-total-breakout-strategy/` | 影子監控＋獨立 Discord |

`telegram_signal_relay.py`、行情監控與 webhook 都是共用基礎設施。
H 訊號可以繼續被記錄，但不納入永豐 2 的純 EF 部位。舊 `pure-ef-hedge-strategy/` 為歷史研究規格。
