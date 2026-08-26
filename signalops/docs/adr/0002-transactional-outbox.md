# ADR 0002：以 Transactional Outbox 發布事件

- 狀態：已接受
- 日期：2026-08-26

## 背景

若匯入器先寫 PostgreSQL、再直接送 Kafka，任何一段網路失敗都可能造成資料庫有事件但 broker 沒事件；反過來也可能讓 worker 處理到尚未 commit 的資料。

## 決策

在同一個資料庫 transaction 內寫入 `signal_events` 與 `outbox_events`。獨立 publisher 鎖定尚未發布的 row，等 Redpanda 確認後才填入 `published_at`。consumer 採至少一次傳遞，並以 `processed_events` 做冪等保護。

## 結果

- 資料庫事實與待發布事件維持原子性。
- publisher crash 後可能重送，因此 consumer 必須冪等。
- outbox 會持續成長；正式環境需設定保留與封存政策。
- 這個設計不承諾 exactly-once，而是把可觀察且可恢復的至少一次語意說清楚。
