# 永豐策略清單

目前只自動啟動 API_KEY 的 H3 MDD加口策略；訊號與行情記錄服務繼續運行。
EF 系列全部停用自動啟動，有券商連線的 EF 策略統一為 Shioaji 模擬帳戶（`simulation=True`）；TOTAL Breakout 只做影子監控、不連券商。
部署重啟後才會套用新設定，切換不會自動處理原有實盤持倉：

| 帳戶 | 策略 | 目錄 | 目前狀態 |
| --- | --- | --- | --- |
| 永豐 1 | EF Hysteresis Again（含多空持續反向部位否決） | `ef-hysteresis-again-strategy/` | 停用自動啟動；`ef-simulation` profile，API_KEY，`simulation=True` |
| 模擬帳戶 | 舊 EF Hysteresis 共識＋01:00 平倉 | `ef-strong-consensus-morning-flat-strategy/` | 同組 API_KEY 憑證，Shioaji `simulation=True` |
| 永豐 2 | 純 EF 合計方向＋多空對稱否決、01:00 清倉 | `ef-morning-weekend-hedge-strategy/` | 停用自動啟動；`ef-simulation` profile，API_KEY2，`simulation=True` |
| 永豐 1（正式） | H3依H_UNIT跟單；已平倉單口回撤達1000點，下一筆Unit+1，回前高恢復Unit | `h3-follow-add-strategy/` | API_KEY／SECRET_KEY，`simulation=False`；獨立帳本 `runtime/api_key.sqlite` |
| 永豐 2（Windows 一鍵啟動） | H3 純跟單依 H_UNIT_KEY2 設定口數，不加碼；H3平倉／反向出場，已移除浮盈回吐平倉，日K均線尚未設定 | `h3-profit-protection-strategy/` | API_KEY2，`BROKER_SIMULATION=False` 真實下單；獨立 `runtime/live/` 帳本；Windows 自動選用 `h3-protection` profile；通知 `DISCORD_H3_TP_WEBHOOK_URL` |
| 不連券商 | EF Hysteresis TOTAL 突破（超越08:45前總部位） | `ef-hysteresis-total-breakout-strategy/` | 影子監控＋獨立 Discord |

`telegram_signal_relay.py`、行情監控與 webhook 都是共用基礎設施。
H 訊號不納入舊永豐 2 純 EF 策略部位；新 H3 純跟單服務啟用後才以獨立帳本跟隨 H3，並與舊 EF 共用帳戶執行鎖。舊 `pure-ef-hedge-strategy/` 為歷史研究規格。
