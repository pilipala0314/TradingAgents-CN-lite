"""fact_financial_report 表迁移 — P2-T3。

Revision ID: 20260708_0007
Revises: 20260708_0006
Create Date: 2026-07-08 14:00:00

- fact_financial_report: 财报关键指标事实表
  - (security_id, report_period, indicator_code, version) 唯一
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "20260708_0007"
down_revision: Union[str, None] = "20260708_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fact_financial_report",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("security_id", UUID(as_uuid=True), nullable=False),
        sa.Column("report_period", sa.Date(), nullable=False),
        sa.Column("indicator_code", sa.String(length=64), nullable=False),
        sa.Column("indicator_name", sa.String(length=128), nullable=True),
        sa.Column("value", sa.Numeric(20, 4), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "security_id", "report_period", "indicator_code", "version",
            name="uq_fact_financial_report_security_period_indicator_version",
        ),
    )
    op.create_index(
        "ix_fact_financial_report_security",
        "fact_financial_report",
        ["security_id"],
        unique=False,
    )
    op.create_index(
        "ix_fact_financial_report_period",
        "fact_financial_report",
        ["report_period"],
        unique=False,
    )
    op.create_index(
        "ix_fact_financial_report_tenant",
        "fact_financial_report",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_fact_financial_report_tenant", table_name="fact_financial_report")
    op.drop_index("ix_fact_financial_report_period", table_name="fact_financial_report")
    op.drop_index("ix_fact_financial_report_security", table_name="fact_financial_report")
    op.drop_table("fact_financial_report")
