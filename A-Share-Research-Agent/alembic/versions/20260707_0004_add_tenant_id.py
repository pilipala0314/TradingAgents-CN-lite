"""add tenant_id column to fact_* tables (P5.3 multi-tenant)

Revision ID: 20260707_0004
Revises: 20260707_0003
Create Date: 2026-07-07 15:00:00

为以下表添加 tenant_id 列（nullable + indexed）：
- fact_market_bar
- fact_capital_flow
- fact_event
- fact_event_raw
- fact_rule_hit
- fact_risk_alert
- fact_strategy_output
- fact_decision_trace
- fact_backtest_run

历史数据不强制回填（保留 NULL = 共享 / 默认租户空间）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260707_0004"
down_revision: Union[str, None] = "20260707_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = (
    "fact_market_bar",
    "fact_capital_flow",
    "fact_event",
    "fact_event_raw",
    "fact_rule_hit",
    "fact_risk_alert",
    "fact_strategy_output",
    "fact_decision_trace",
    "fact_backtest_run",
)


def upgrade() -> None:
    for tbl in _TABLES:
        op.add_column(
            tbl,
            sa.Column("tenant_id", sa.String(length=64), nullable=True),
        )
        op.create_index(
            op.f(f"ix_{tbl}_tenant_id"),
            tbl,
            ["tenant_id"],
            unique=False,
        )


def downgrade() -> None:
    for tbl in _TABLES:
        op.drop_index(op.f(f"ix_{tbl}_tenant_id"), table_name=tbl)
        op.drop_column(tbl, "tenant_id")
