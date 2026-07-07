"""dim_watchlist + map_watchlist_item 表迁移 — P1-T1。

Revision ID: 20260708_0006
Revises: 20260708_0005
Create Date: 2026-07-08 13:00:00

- dim_watchlist: 自选股 list 元数据（按 tenant 隔离 + name 唯一）
- map_watchlist_item: list → symbol 映射（symbol 索引）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "20260708_0006"
down_revision: Union[str, None] = "20260708_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dim_watchlist",
        sa.Column("watchlist_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("watchlist_uuid", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("watchlist_uuid", name="uq_dim_watchlist_uuid"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_dim_watchlist_tenant_name"),
    )
    op.create_index(
        "ix_dim_watchlist_tenant",
        "dim_watchlist",
        ["tenant_id"],
        unique=False,
    )

    op.create_table(
        "map_watchlist_item",
        sa.Column("item_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "watchlist_id",
            sa.BigInteger(),
            sa.ForeignKey("dim_watchlist.watchlist_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "watchlist_id", "symbol",
            name="uq_map_watchlist_item_watchlist_symbol",
        ),
    )
    op.create_index(
        "ix_map_watchlist_item_symbol",
        "map_watchlist_item",
        ["symbol"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_map_watchlist_item_symbol", table_name="map_watchlist_item")
    op.drop_table("map_watchlist_item")
    op.drop_index("ix_dim_watchlist_tenant", table_name="dim_watchlist")
    op.drop_table("dim_watchlist")
