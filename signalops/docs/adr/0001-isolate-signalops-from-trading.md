# ADR 0001：SignalOps 與交易執行隔離

- 狀態：已接受
- 日期：2026-08-26

## 背景

這個 repository 包含接近正式用途的監控與交易整合程式，但作品網站需要能安全公開部署。若直接提供既有 CSV，或讓 API import 下單模組，demo 的可用性、密鑰與個人帳務資料就會綁在一起。

## 決策

新增獨立子目錄 `signalops/`，其中的 `backend-signalops-py` 只接受具版本的 `SignalEvent` 契約、保存去識別化事件並提供唯讀 API；`frontend-react` 是其 web client。舊資料只能經過明確的 importer 跨越邊界，帳號會被雜湊，自由文字則被丟棄。

AI 助手使用與 HTTP API 相同的唯讀 application services，不得 import 或呼叫交易執行程式。

## 結果

- 訊號收集與作品展示可以獨立演進。
- 公開 demo 不需要券商憑證。
- 事件契約與 importer 會產生少量重複程式碼，但換來清楚的安全邊界。
- 即時與事件平台可逐步加入，不阻塞最初的產品功能。
