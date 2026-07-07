"""fact_macro_indicator 表迁移 — P0-T5。

Revision ID: 20260708_0005
Revises: 20260707_0004
Create Date: 2026-07-08 12:00:00

新建 fact_macro_indicator 表：
- 自然唯一键 (indicator_code, period, version, tenant_id)
- 索引 (indicator_code, as_of) 用于历史回溯
- 索引 tenant_id 用于多租户隔离查询
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "20260708_0005"
down_revision: Union[str, None] = "20260707_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fact_macro_indicator",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("indicator_code", sa.String(length=32), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("as_of", sa.String(length=32), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "indicator_code", "period", "version", "tenant_id",
            name="uq_fact_macro_indicator_natural_key",
        ),
    )
    op.create_index(
        "ix_fact_macro_indicator_code_asof",
        "fact_macro_indicator",
        ["indicator_code", "as_of"],
        unique=False,
    )
    op.create_index(
        "ix_fact_macro_indicator_tenant",
        "fact_macro_indicator",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_fact_macro_indicator_tenant", table_name="fact_macro_indicator")
    op.drop_index("ix_fact_macro_indicator_code_asof", table_name="fact_macro_indicator")
    op.drop_table("fact_macro_indicator")
