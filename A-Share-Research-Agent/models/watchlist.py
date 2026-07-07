"""Watchlist ORM（P1-T1）。

- `dim_watchlist`     自选股 list（按租户隔离）
- `map_watchlist_item` list → symbol 映射
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from .base import Base


class DimWatchlist(Base):
    """自选股 list 元数据。"""

    __tablename__ = "dim_watchlist"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_dim_watchlist_tenant_name"),
        Index("ix_dim_watchlist_tenant", "tenant_id"),
    )

    watchlist_id = Column(
        BigInteger, primary_key=True, autoincrement=True,
    )
    watchlist_uuid = Column(
        UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False,
    )
    name = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class MapWatchlistItem(Base):
    """自选股 list → 标的 映射。"""

    __tablename__ = "map_watchlist_item"
    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "symbol",
            name="uq_map_watchlist_item_watchlist_symbol",
        ),
        Index("ix_map_watchlist_item_symbol", "symbol"),
    )

    item_id = Column(BigInteger, primary_key=True, autoincrement=True)
    watchlist_id = Column(
        BigInteger,
        ForeignKey("dim_watchlist.watchlist_id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol = Column(String(32), nullable=False)
    note = Column(String(256), nullable=True)
    added_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False,
    )
