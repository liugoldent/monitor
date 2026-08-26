"""create signal events

Revision ID: 20260826_0001
Revises:
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260826_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("instrument", sa.String(length=32), nullable=False),
        sa.Column("strategy_code", sa.String(length=80), nullable=False),
        sa.Column("strategy_name", sa.String(length=160), nullable=False),
        sa.Column("account_ref", sa.String(length=21), nullable=True),
        sa.Column("previous_position", sa.SmallInteger(), nullable=False),
        sa.Column("new_position", sa.SmallInteger(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("reference_price", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("schema_version = 1", name="ck_signal_events_schema_v1"),
        sa.CheckConstraint("previous_position BETWEEN -1 AND 1", name="ck_previous_position"),
        sa.CheckConstraint("new_position BETWEEN -1 AND 1", name="ck_new_position"),
        sa.CheckConstraint("quantity > 0", name="ck_positive_quantity"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fingerprint"),
    )
    op.create_index(
        "ix_signal_events_strategy_timeline",
        "signal_events",
        ["strategy_code", "occurred_at"],
    )
    op.create_index("ix_signal_events_timeline", "signal_events", ["occurred_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_signal_events_timeline", table_name="signal_events")
    op.drop_index("ix_signal_events_strategy_timeline", table_name="signal_events")
    op.drop_table("signal_events")
