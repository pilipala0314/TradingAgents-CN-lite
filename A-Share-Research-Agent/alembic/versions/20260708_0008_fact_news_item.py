"""fact_news_item 表迁移 — P2-T5。

Revision ID: 20260708_0008
Revises: 20260708_0007
Create Date: 2026-07-08 15:00:00

- fact_news_item: 舆情新闻条目（每行 = 一条新闻 + sentiment_score/label）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "20260708_0008"
down_revision: Union[str, None] = "20260708_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fact_news_item",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("security_id", UUID(as_uuid=True), nullable=True),
        sa.Column("symbol", sa.String(length=16), nullable=True),
        sa.Column("news_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_name", sa.String(length=64), nullable=True),
        sa.Column("sentiment_score", sa.Numeric(6, 4), nullable=True),
        sa.Column("sentiment_label", sa.String(length=16), nullable=True),
        sa.Column("score_method", sa.String(length=32), nullable=True),
        sa.Column("raw_payload", JSONB, nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("url", name="uq_fact_news_item_url"),
    )
    op.create_index("ix_fact_news_item_security", "fact_news_item", ["security_id"], unique=False)
    op.create_index("ix_fact_news_item_symbol", "fact_news_item", ["symbol"], unique=False)
    op.create_index("ix_fact_news_item_news_id", "fact_news_item", ["news_id"], unique=False)
    op.create_index("ix_fact_news_item_published_at", "fact_news_item", ["published_at"], unique=False)
    op.create_index("ix_fact_news_item_tenant", "fact_news_item", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_fact_news_item_tenant", table_name="fact_news_item")
    op.drop_index("ix_fact_news_item_published_at", table_name="fact_news_item")
    op.drop_index("ix_fact_news_item_news_id", table_name="fact_news_item")
    op.drop_index("ix_fact_news_item_symbol", table_name="fact_news_item")
    op.drop_index("ix_fact_news_item_security", table_name="fact_news_item")
    op.drop_table("fact_news_item")
