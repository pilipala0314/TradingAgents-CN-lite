"""add fact_event_raw table + fact_event.severity column

Revision ID: 20260701_0002
Revises: 20260701_0001
Create Date: 2026-07-01 17:00:00

新增
  - fact_event_raw      事件原始层（审计/回放/重跑）
  - fact_event.severity 离散分级列（LOW/MEDIUM/HIGH/CRITICAL）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260701_0002"
down_revision: Union[str, None] = "20260701_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) fact_event 增加 severity 列
    op.add_column(
        "fact_event",
        sa.Column("severity", sa.String(length=16), nullable=True),
    )
    op.create_index(
        op.f("ix_fact_event_severity"),
        "fact_event",
        ["severity"],
        unique=False,
    )

    # 2) 新增 fact_event_raw 表
    op.create_table(
        "fact_event_raw",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_fact_event_raw"),
        sa.UniqueConstraint("event_id", name="uq_fact_event_raw_event_id"),
    )
    op.create_index(
        op.f("ix_fact_event_raw_business_date"),
        "fact_event_raw",
        ["business_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fact_event_raw_source"),
        "fact_event_raw",
        ["source"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_fact_event_raw_source"), table_name="fact_event_raw")
    op.drop_index(op.f("ix_fact_event_raw_business_date"), table_name="fact_event_raw")
    op.drop_table("fact_event_raw")
    op.drop_index(op.f("ix_fact_event_severity"), table_name="fact_event")
    op.drop_column("fact_event", "severity")
