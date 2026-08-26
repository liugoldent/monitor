from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from signalops.db import Base


class SignalEventModel(Base):
    __tablename__ = "signal_events"
    __table_args__ = (
        CheckConstraint("schema_version = 1", name="ck_signal_events_schema_v1"),
        CheckConstraint("previous_position BETWEEN -1 AND 1", name="ck_previous_position"),
        CheckConstraint("new_position BETWEEN -1 AND 1", name="ck_new_position"),
        CheckConstraint("quantity > 0", name="ck_positive_quantity"),
        Index("ix_signal_events_timeline", "occurred_at", "id"),
        Index("ix_signal_events_strategy_timeline", "strategy_code", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    instrument: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_code: Mapped[str] = mapped_column(String(80), nullable=False)
    strategy_name: Mapped[str] = mapped_column(String(160), nullable=False)
    account_ref: Mapped[str | None] = mapped_column(String(21))
    previous_position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    new_position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("publish_attempts >= 0", name="ck_outbox_publish_attempts"),
        Index("ix_outbox_unpublished", "published_at", "occurred_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
