# SignalOps Helm Chart

這個 chart 部署 API、outbox publisher 與 Go replay worker。PostgreSQL、Redpanda／Kafka、Ingress Controller、cert-manager 與 secret manager 視為平台服務，不在 application chart 內建立。

```bash
helm lint .
helm template signalops . --set api.image=registry.example/signalops-api \
  --set worker.image=registry.example/signal-replay-worker
```

正式安裝前必須先建立 `signalops-runtime` Secret，建議使用 External Secrets 或雲端 secret manager。`values.yaml` 不應放任何明文憑證。
