# Monitor 與 SignalOps AI

這個 repository 保留原有的期貨／股票監控程式，並在 `signalops/` 集中一個可公開展示的全端 side project：**SignalOps AI**。它受到 ESBI 四象限啟發，把依賴個人維護的程式交易逐步轉成可量測、可委派的 BI 營運系統。策略進出場 CSV 會轉成去識別化事件，並提供即時看板、營運分析、唯讀 AI 小助手與可重播事件架構。

## 作品涵蓋的技術

- React 19、TypeScript、Vite、Vitest
- Python、FastAPI、Pydantic、SQLAlchemy、Alembic
- PostgreSQL、WebSocket、SSE
- OpenAI Responses API 與 strict tool calling
- Transactional outbox、Redpanda／Kafka、Go worker
- Docker Compose、Prometheus、Grafana、OpenTelemetry
- GitHub Actions、Helm、Terraform

## 快速啟動

```bash
cd signalops
docker compose up --build -d
cd frontend-react
pnpm install
pnpm dev --host 0.0.0.0 --port 5373
```

開啟 `http://localhost:5373`。API 是 `http://localhost:8000`，OpenAPI 文件是 `http://localhost:8000/docs`。

若要一起啟動 Redpanda、Go worker 與監控服務，請參考 [操作手冊](docs/runbook.md)。

## 重要安全邊界

SignalOps 是唯讀展示與維運系統，不是下單系統。它不需要券商憑證，不保存原始帳號與自由文字訊號，也不提供 AI 交易工具。來源資料缺少可靠成交價，因此介面刻意不顯示虛構損益。

## 文件導覽

- [系統架構](docs/architecture.md)
- [ESBI 與 BI 產品願景](docs/product-vision.md)
- [SignalOps 與交易執行隔離](docs/adr/0001-isolate-signalops-from-trading.md)
- [Transactional outbox 決策](docs/adr/0002-transactional-outbox.md)
- [操作手冊](docs/runbook.md)
- [Redpanda 中斷演練](docs/incident-exercise.md)
- [後端開發說明](backend-signalops-py/README.md)
- [前端開發說明](frontend-react/README.md)

## 目前交付邊界

程式、容器、CI、Helm 與 Terraform 都可在本機檢查；實際雲端套用需要使用者選定 AWS 帳號、網域、預算與 secret 管理方式後才執行。OpenAI 小助手在沒有 API key 時會使用本機唯讀工具回答，因此 clone 後仍能完整展示。
