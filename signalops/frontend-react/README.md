# SignalOps AI Web

這是 SignalOps AI 的 React 19＋TypeScript 前端，包含策略總覽、即時事件卡片、SSE 策略小助手，以及保留的市場 K 線展示頁。介面採用日系編輯設計，以和紙白、墨色與朱砂紅建立視覺層級，並支援會保存偏好的日間／夜間模式。

## 開發環境

需要 Node.js 20.19 以上與 pnpm 10。先在 `monitor/signalops` 啟動 API：

```bash
docker compose up --build -d
```

再啟動前端：

```bash
cd frontend-react
pnpm install
pnpm dev --host 0.0.0.0 --port 5373
```

Vite 會把 `/api` 與 WebSocket proxy 到 `http://localhost:8000`。若前後端分開部署，請設定 `VITE_SIGNALOPS_API_URL`。

## 品質檢查

```bash
pnpm test
pnpm type-check
pnpm build
```

測試涵蓋外觀模式切換、事件卡片的隱私欄位、overview render，以及助手 SSE parser。正式 build 不會讀取原始策略事件 CSV。

## 頁面

- `#/overview`：策略數量、事件統計、目前持倉與策略摘要。
- `#/analytics`：ESBI 產品故事、營運 KPI、月趨勢、轉換矩陣與資料品質。
- `#/signals`：游標分頁事件卡片，支援策略／動作篩選與 WebSocket 更新。
- `#/assistant`：只查詢真實資料的策略小助手，不提供下單或保證獲利建議。
- `#/market`：既有 K 線資料展示。
- `#/react-notes`：作品的 React 技術重點。
