import argparse
import json
import time
from datetime import UTC, datetime

from kafka import KafkaProducer
from sqlalchemy import select

from signalops.config import settings
from signalops.db import SessionLocal
from signalops.models import OutboxEventModel


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="把未發布的 outbox 事件送到 Kafka。")
    parser.add_argument("--once", action="store_true", help="發布一個批次後結束。")
    parser.add_argument("--batch-size", type=int, default=100, help="每批最多事件數。")
    return parser


def publish_batch(producer: KafkaProducer, batch_size: int) -> int:
    published = 0
    with SessionLocal.begin() as session:
        events = list(
            session.scalars(
                select(OutboxEventModel)
                .where(OutboxEventModel.published_at.is_(None))
                .order_by(OutboxEventModel.occurred_at, OutboxEventModel.id)
                .with_for_update(skip_locked=True)
                .limit(batch_size)
            )
        )
        for event in events:
            try:
                producer.send(
                    settings.kafka_topic,
                    key=event.aggregate_id.encode(),
                    value=event.payload,
                    headers=[
                        ("event_id", str(event.id).encode()),
                        ("event_type", event.event_type.encode()),
                    ],
                ).get(timeout=10)
                event.published_at = datetime.now(UTC)
                event.publish_attempts += 1
                event.last_error = None
                published += 1
            except Exception as exc:
                event.publish_attempts += 1
                event.last_error = str(exc)[:500]
                raise
    return published


def main() -> None:
    args = build_parser().parse_args()
    producer = KafkaProducer(
        bootstrap_servers=settings.kafka_brokers.split(","),
        acks="all",
        retries=10,
        max_in_flight_requests_per_connection=1,
        value_serializer=lambda value: json.dumps(
            value, ensure_ascii=False, separators=(",", ":")
        ).encode(),
    )
    try:
        while True:
            count = publish_batch(producer, max(1, min(args.batch_size, 1000)))
            if count:
                print(f"已發布 {count} 筆 outbox 事件")
            if args.once:
                return
            if count == 0:
                time.sleep(settings.outbox_poll_seconds)
    finally:
        producer.flush(timeout=10)
        producer.close()


if __name__ == "__main__":
    main()
