# Signal Replay Worker

這個 Go worker 以 consumer group 讀取 `signal.events.v1`，採用至少一次傳遞。每筆事件會先寫入 `processed_events`，重複 event ID 不會再次更新 projection；database transaction 成功後才提交 Kafka offset。

`strategy_projections` 是可丟棄並重建的 read model，原始事實仍以
`signal_events` 為準。這個 worker 沒有交易或下單權限。

## 測試與建置

本機有 Go 1.24 時可直接執行：

```bash
go test ./...
go build ./...
```

也可在 `monitor/signalops` 使用 multi-stage Dockerfile：

```bash
docker build --target test -f workers/signal-replay-go/Dockerfile .
```
