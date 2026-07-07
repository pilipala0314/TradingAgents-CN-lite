"""Watchlist Service — 自选股 list CRUD（P1-T1）。

- 按 tenant_id 隔离（None=default）
- list 创建后可通过 POST /api/watchlist/{id}/items 加 symbol
- 加 item 幂等（重复 symbol 静默忽略）
- 删 item 幂等
"""
from __future__ import annotations

import logging
import uuid as uuid_mod
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.watchlist import DimWatchlist, MapWatchlistItem

logger = logging.getLogger(__name__)


async def list_watchlists(
    session: AsyncSession,
    *,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """列出当前租户下所有自选股 list（含 item_count）。"""
    stmt = (
        select(DimWatchlist)
        .where(DimWatchlist.tenant_id == tenant_id)
        .order_by(DimWatchlist.watchlist_id.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    out: list[dict[str, Any]] = []
    for w in rows:
        item_count = await _count_items(session, w.watchlist_id)
        out.append({
            "watchlist_id": w.watchlist_id,
            "watchlist_uuid": str(w.watchlist_uuid),
            "name": w.name,
            "description": w.description,
            "item_count": item_count,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "updated_at": w.updated_at.isoformat() if w.updated_at else None,
        })
    return out


async def create_watchlist(
    session: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """新建 list。同 (tenant, name) 重复 → 抛 IntegrityError 让 router 转 409。"""
    w = DimWatchlist(
        name=name,
        description=description,
        tenant_id=tenant_id,
        watchlist_uuid=uuid_mod.uuid4(),
    )
    session.add(w)
    await session.commit()
    await session.refresh(w)
    return {
        "watchlist_id": w.watchlist_id,
        "watchlist_uuid": str(w.watchlist_uuid),
        "name": w.name,
        "description": w.description,
        "item_count": 0,
    }


async def get_watchlist(
    session: AsyncSession,
    *,
    watchlist_id: int,
) -> dict[str, Any] | None:
    w = await session.get(DimWatchlist, watchlist_id)
    if w is None:
        return None
    items = await _list_items(session, watchlist_id)
    return {
        "watchlist_id": w.watchlist_id,
        "watchlist_uuid": str(w.watchlist_uuid),
        "name": w.name,
        "description": w.description,
        "item_count": len(items),
        "items": items,
    }


async def add_item(
    session: AsyncSession,
    *,
    watchlist_id: int,
    symbol: str,
    note: str | None = None,
) -> dict[str, Any] | None:
    """加 symbol 到 list（幂等：重复不报错）。返回最新 item 详情，list 不存在则 None。"""
    w = await session.get(DimWatchlist, watchlist_id)
    if w is None:
        return None
    try:
        item = MapWatchlistItem(
            watchlist_id=watchlist_id, symbol=symbol, note=note,
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return _item_to_dict(item)
    except IntegrityError:
        # 重复 symbol → 已存在，返回原 item
        await session.rollback()
        stmt = select(MapWatchlistItem).where(
            MapWatchlistItem.watchlist_id == watchlist_id,
            MapWatchlistItem.symbol == symbol,
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing is None:
            return None
        return _item_to_dict(existing)


async def remove_item(
    session: AsyncSession,
    *,
    watchlist_id: int,
    symbol: str,
) -> bool:
    """从 list 移除 symbol（幂等：不存在返 False）。"""
    stmt = delete(MapWatchlistItem).where(
        MapWatchlistItem.watchlist_id == watchlist_id,
        MapWatchlistItem.symbol == symbol,
    )
    result = await session.execute(stmt)
    await session.commit()
    return (result.rowcount or 0) > 0


async def delete_watchlist(
    session: AsyncSession,
    *,
    watchlist_id: int,
) -> bool:
    """删除 list（级联删 item）。"""
    w = await session.get(DimWatchlist, watchlist_id)
    if w is None:
        return False
    await session.delete(w)
    await session.commit()
    return True


# ── 内部 helper ──────────────────────────────────


async def _count_items(session: AsyncSession, watchlist_id: int) -> int:
    from sqlalchemy import func
    stmt = select(func.count()).select_from(MapWatchlistItem).where(
        MapWatchlistItem.watchlist_id == watchlist_id,
    )
    return int((await session.execute(stmt)).scalar() or 0)


async def _list_items(
    session: AsyncSession, watchlist_id: int,
) -> list[dict[str, Any]]:
    stmt = (
        select(MapWatchlistItem)
        .where(MapWatchlistItem.watchlist_id == watchlist_id)
        .order_by(MapWatchlistItem.added_at.asc())
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_item_to_dict(r) for r in rows]


def _item_to_dict(item: MapWatchlistItem) -> dict[str, Any]:
    return {
        "item_id": item.item_id,
        "watchlist_id": item.watchlist_id,
        "symbol": item.symbol,
        "note": item.note,
        "added_at": item.added_at.isoformat() if item.added_at else None,
    }
