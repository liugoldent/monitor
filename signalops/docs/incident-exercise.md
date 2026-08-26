# 事件演練：Redpanda 中斷 15 分鐘

## 情境

Redpanda 無法接受事件，但 PostgreSQL 與 API 正常。目標是證明核心查詢不受影響，且訊息系統恢復後不遺失事件。

## 預期行為

- importer 仍能把事件與 outbox row 原子寫入 PostgreSQL。
- overview、signals、WebSocket 與本機助手仍可查詢事實表。
- publisher 失敗並累積未發布 outbox；Go projection 暫停更新。
- broker 恢復後 backlog 下降，重送不會讓同一 `event_id` 重複更新 projection。

## 演練步驟

1. 記錄四張表的 row count 與最新時間。
2. 停止 `redpanda`，再匯入一小批測試事件。
3. 確認 API 仍回應，且 `outbox_events WHERE published_at IS NULL` 增加。
4. 啟動 `redpanda`、publisher 與 worker。
5. 等 backlog 清零，確認新增事件都存在於 `processed_events`。
6. 重新播放同一批訊息，確認 projection 結果不變。

## 通過條件

- 事實表零遺失、零未預期重複。
- API SLO 未因 broker 中斷而失效。
- 恢復後所有合法事件最終完成 projection。
- 操作者能只靠 dashboard、metrics 與 log 找出失敗位置。
