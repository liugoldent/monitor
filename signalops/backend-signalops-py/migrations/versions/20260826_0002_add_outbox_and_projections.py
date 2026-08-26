"""新增 transactional outbox 與 replay projection 資料表

Revision ID: 20260826_0002
Revises: 20260826_0001
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0002"
down_revision: str | None = "20260826_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.String(length=80), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publish_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("publish_attempts >= 0", name="ck_outbox_publish_attempts"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_unpublished",
        "outbox_events",
        ["published_at", "occurred_at"],
    )
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_table(
        "strategy_projections",
        sa.Column("strategy_code", sa.String(length=80), nullable=False),
        sa.Column("strategy_name", sa.String(length=160), nullable=False),
        sa.Column("instrument", sa.String(length=32), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("last_event_id", sa.Uuid(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("position BETWEEN -1 AND 1", name="ck_projection_position"),
        sa.PrimaryKeyConstraint("strategy_code"),
    )

    # 讓既有事件也能由 Kafka replay，不要求重新匯入原始 CSV。
    op.execute(
        """
        INSERT INTO outbox_events (
            id, aggregate_type, aggregate_id, event_type, payload,
            occurred_at, publish_attempts
        )
        SELECT
            (
                substr(md5(id::text || 'outbox'), 1, 8) || '-' ||
                substr(md5(id::text || 'outbox'), 9, 4) || '-' ||
                substr(md5(id::text || 'outbox'), 13, 4) || '-' ||
                substr(md5(id::text || 'outbox'), 17, 4) || '-' ||
                substr(md5(id::text || 'outbox'), 21, 12)
            )::uuid,
            'signal_event',
            id::text,
            'signal.event.v1',
            json_build_object(
                'id', id,
                'schema_version', schema_version,
                'occurred_at', occurred_at,
                'received_at', received_at,
                'source', source,
                'instrument', instrument,
                'strategy_code', strategy_code,
                'strategy_name', strategy_name,
                'account_ref', account_ref,
                'previous_position', previous_position,
                'new_position', new_position,
                'action', action,
                'side', side,
                'quantity', quantity,
                'reference_price', reference_price,
                'attributes', attributes
            ),
            occurred_at,
            0
        FROM signal_events
        """
    )


def downgrade() -> None:
    op.drop_table("strategy_projections")
    op.drop_table("processed_events")
    op.drop_index("ix_outbox_unpublished", table_name="outbox_events")
    op.drop_table("outbox_events")
