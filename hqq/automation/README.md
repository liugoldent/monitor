# HQQ 既有 Chrome 分頁截圖

六策略 Telegram 監聽器收到有效且去重後的倉位轉換時，會操作**既有** Chrome 裡唯一一個
`https://hr-att.web.app/` 分頁：將它切到前景、重整，並把該 Chrome 視窗截圖存到 Downloads。
若重整後 Chrome 顯示唯一一個「以 keedem.l 的身分繼續」Google 原生確認按鈕，流程會點擊它以恢復既有 HQQ session。
它不會呼叫 HQQ API、不會輸入 Google 密碼、不會點擊打卡按鈕，也不會送出表單。

## 一次性準備

1. 正常開啟 `keedem.l` 的 Chrome profile。
2. 在這個 profile 手動登入 HQQ：<https://hr-att.web.app/>。
3. 保持這一個 HQQ 分頁開著；建議釘選。
4. 所有其他 Chrome profile 都可以正常使用，但不要再開第二個 HQQ 分頁。

收到 Telegram 訊號時，HQQ 視窗會短暫切到最前面並停留在 HQQ，然後輸出截圖到
`/Users/kt/Downloads`。若 HQQ 分頁被關閉或同時有兩個，流程會安全失敗並在監聽器日誌記錄原因。

## macOS 權限

第一次觸發時，macOS 可能要求允許執行監聽器的 Terminal／Python「自動化」控制 Google Chrome、
「輔助使用」以取得並以前景化唯一的 HQQ 原生視窗，以及「螢幕錄製與系統音訊」權限以精準擷取該視窗。這些權限只用於切換、重整與截圖你已經開啟的 HQQ 分頁。
