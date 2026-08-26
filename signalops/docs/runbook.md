# SignalOps 操作手冊

## 本機啟動

只啟動核心 API 與 PostgreSQL：

```bash
cd signalops
docker compose up --build -d
```

加入事件串流與可觀測性：

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
docker compose --profile streaming --profile observability up --build -d
```

服務入口：

| 服務 | 網址 |
| --- | --- |
| API 與 OpenAPI | `http://localhost:8000`、`http://localhost:8000/docs` |
| Redpanda Console | `http://localhost:8088` |
| Prometheus | `http://localhost:9090` |
| Grafana | `http://localhost:3000` |

## 健康檢查

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/readyz
curl -fsS http://localhost:8000/api/v1/overview
curl -fsS http://localhost:8000/metrics/
docker compose ps
```

資料一致性查核：

```sql
SELECT count(*) FROM signal_events;
SELECT count(*) FROM outbox_events WHERE published_at IS NULL;
SELECT count(*) FROM processed_events;
SELECT count(*) FROM strategy_projections;
```

短時間內 `processed_events` 可以落後 `signal_events`；publisher 與 worker 正常後應持續追上。`strategy_projections` 的 row 數應接近不同 `strategy_code` 的數量。

## 常見告警與處理

### API 5xx 上升

1. 查看 Grafana 的錯誤率與 latency。
2. 查看 `api` service log，確認是 database、validation 或上游 LLM。
3. 若只有 OpenAI 故障，助手會自動降級成本機唯讀回答，不應影響 overview 與 signals。
4. 若 database 不可用，先恢復 PostgreSQL；不要刪除 volume 或強制重建 migration history。

### Outbox backlog 上升

1. 確認 Redpanda health 與 topic `signal.events.v1`。
2. 查看 publisher 的 `last_error` 與 container log。
3. 修復 broker 後重啟 publisher；不要手動把 `published_at` 設成成功。

### Consumer lag 上升

1. 查看 Go worker是否反覆 crash 或無法連 PostgreSQL。
2. 確認 `processed_events` 是否成長。
3. 可以水平擴充同一 consumer group；projection 與 dedupe transaction 會保護重送。
4. 若遇到不合法事件，先保存原 payload 與 offset，再決定修 producer 或送往 dead-letter topic。

## 回復與回滾

- API／worker image 可回滾到前一個 immutable tag；Alembic migration 不應在未備份時直接 downgrade。
- projection 可停止 worker、清除「明確指定的 projection 表」並從 topic replay；不可清除 `signal_events`。
- PostgreSQL 恢復演練需使用獨立測試 database，先驗證 row count 與最後事件時間再切換。

## 關閉

```bash
docker compose --profile streaming --profile observability down
```

不要加 `-v`，除非你明確要刪除本機 PostgreSQL、Redpanda 與 Grafana 資料。
