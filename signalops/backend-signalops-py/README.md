# SignalOps API

這個 FastAPI 服務是既有交易程式與公開作品之間的隱私邊界。它只接受去識別化的 `SignalEvent`，沒有任何下單能力。

## 本機開發

在 `monitor/signalops` 執行：

```bash
docker compose up --build -d
```

API 位於 `http://localhost:8000`，互動式 OpenAPI 位於 `/docs`，PostgreSQL 對本機開放 `5434` port。

若要直接在本機跑測試：

```bash
python3 -m venv backend-signalops-py/.venv
backend-signalops-py/.venv/bin/pip install -e 'backend-signalops-py[dev]'
backend-signalops-py/.venv/bin/ruff check backend-signalops-py
backend-signalops-py/.venv/bin/pytest -q backend-signalops-py
```

## 匯入既有 CSV

```bash
export SIGNALOPS_ACCOUNT_SALT='請換成足夠長的本機隨機字串'
backend-signalops-py/.venv/bin/signalops-import \
  ../backend-futures-py/tv_doc/six_strategy_signal_events.csv
```

缺少時間戳的 row 會被報告並跳過。`signal` 自由文字與原始帳號絕不落地；帳號只會變成加鹽雜湊 reference。每筆事實事件與 outbox row 會在同一個 transaction 寫入。

## API

| 端點 | 用途 |
| --- | --- |
| `GET /healthz` | 不依賴 database 的存活檢查 |
| `GET /readyz` | PostgreSQL readiness 檢查 |
| `GET /api/v1/signals` | 游標分頁、可篩選的事件時間軸 |
| `GET /api/v1/overview` | 事件、策略與持倉摘要 |
| `GET /api/v1/positions` | 每個策略的最新持倉 |
| `GET /api/v1/analytics` | 活躍度、曝險比例、趨勢、轉換矩陣與資料品質 |
| `WS /api/v1/stream` | 新事件與 heartbeat |
| `POST /api/v1/assistant/query` | SSE 唯讀策略問答 |
| `GET /metrics/` | Prometheus 指標 |

## AI 助手模式

- `SIGNALOPS_ASSISTANT_MODE=auto`：有 `OPENAI_API_KEY` 時使用 OpenAI，否則使用本機回答。
- `SIGNALOPS_ASSISTANT_MODE=openai`：要求 OpenAI；服務失敗時仍降級到本機工具。
- 其他值：只使用本機工具。

助手只註冊 `list_recent_signals`、`get_current_positions`、`get_strategy_summary`、`get_business_analytics` 四個 strict 唯讀工具。正式 OpenAI 串流採 Responses API；資料不要求保存（`store=False`）。

## 資料庫 migration

```bash
cd backend-signalops-py
.venv/bin/alembic upgrade head
```

不要用 ORM 自動建立正式 schema；所有變更都必須經 Alembic migration。

## 小助手回歸評估

評估會連到去識別化 database，檢查本機回答包含必要限制且不出現保證獲利或下單文字：

```bash
.venv/bin/signalops-eval evals/assistant_cases.jsonl
```

這是 deterministic baseline，不取代正式模型的離線／線上品質評估。若更換 prompt、tool schema 或模型，應另外記錄工具選擇正確率、引用完整率、延遲與成本。
