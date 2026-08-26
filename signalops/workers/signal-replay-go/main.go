package main

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/twmb/franz-go/pkg/kgo"
)

type config struct {
	Brokers     []string
	Topic       string
	Group       string
	DatabaseURL string
}

func loadConfig() config {
	return config{
		Brokers:     strings.Split(envOr("SIGNALOPS_KAFKA_BROKERS", "redpanda:9092"), ","),
		Topic:       envOr("SIGNALOPS_KAFKA_TOPIC", "signal.events.v1"),
		Group:       envOr("SIGNALOPS_KAFKA_GROUP", "signalops-replay-v1"),
		DatabaseURL: envOr("SIGNALOPS_DATABASE_URL", "postgresql://signalops:signalops@db:5432/signalops"),
	}
}

func envOr(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func projectEvent(ctx context.Context, pool *pgxpool.Pool, event SignalEvent) error {
	if err := event.Validate(); err != nil {
		return err
	}
	tx, err := pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx) //nolint:errcheck

	var eventID string
	err = tx.QueryRow(
		ctx,
		`INSERT INTO processed_events (event_id)
		 VALUES ($1) ON CONFLICT DO NOTHING RETURNING event_id`,
		event.ID,
	).Scan(&eventID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}

	_, err = tx.Exec(
		ctx,
		`INSERT INTO strategy_projections (
			strategy_code, strategy_name, instrument, position, quantity,
			last_event_id, last_event_at
		) VALUES ($1, $2, $3, $4, $5, $6, $7)
		ON CONFLICT (strategy_code) DO UPDATE SET
			strategy_name = EXCLUDED.strategy_name,
			instrument = EXCLUDED.instrument,
			position = EXCLUDED.position,
			quantity = EXCLUDED.quantity,
			last_event_id = EXCLUDED.last_event_id,
			last_event_at = EXCLUDED.last_event_at
		WHERE strategy_projections.last_event_at <= EXCLUDED.last_event_at`,
		event.StrategyCode,
		event.StrategyName,
		event.Instrument,
		event.NewPosition,
		event.Quantity,
		event.ID,
		event.OccurredAt,
	)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func main() {
	cfg := loadConfig()
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	pool, err := pgxpool.New(ctx, cfg.DatabaseURL)
	if err != nil {
		log.Fatalf("建立 PostgreSQL pool 失敗：%v", err)
	}
	defer pool.Close()

	consumer, err := kgo.NewClient(
		kgo.SeedBrokers(cfg.Brokers...),
		kgo.ConsumerGroup(cfg.Group),
		kgo.ConsumeTopics(cfg.Topic),
		kgo.DisableAutoCommit(),
	)
	if err != nil {
		log.Fatalf("建立 Kafka consumer 失敗：%v", err)
	}
	defer consumer.Close()

	log.Printf("開始消費 topic=%s group=%s", cfg.Topic, cfg.Group)
	for {
		fetches := consumer.PollFetches(ctx)
		if ctx.Err() != nil {
			return
		}
		if errs := fetches.Errors(); len(errs) > 0 {
			log.Printf("Kafka fetch 錯誤：%v", errs)
			continue
		}
		for _, record := range fetches.Records() {
			var event SignalEvent
			if err := json.Unmarshal(record.Value, &event); err != nil {
				log.Printf("忽略無法解析的事件：%v", err)
				continue
			}
			if err := projectEvent(ctx, pool, event); err != nil {
				log.Printf("projection 失敗 event=%s：%v", event.ID, err)
				continue
			}
			if err := consumer.CommitRecords(ctx, record); err != nil {
				log.Printf("提交 Kafka offset 失敗：%v", err)
			}
		}
	}
}
