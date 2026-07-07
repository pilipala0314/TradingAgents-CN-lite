"""Watchlist API — 自选股 list CRUD（P1-T1）。

- GET    /api/watchlist                       列出当前租户所有 list
- POST   /api/watchlist                       新建 list
- GET    /api/watchlist/{watchlist_id}        拉单 list + items
- POST   /api/watchlist/{watchlist_id}/items  加 symbol
- DELETE /api/watchlist/{watchlist_id}/items/{symbol}  移除 symbol
- DELETE /api/watchlist/{watchlist_id}        删 list（级联）
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from services.watchlist_service import (
    add_item,
    create_watchlist,
    delete_watchlist,
    get_watchlist,
    list_watchlists,
    remove_item,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class CreateWatchlistRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=512)


class AddItemRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=256)


@router.get("")
async def list_all(
    tenant_id: str | None = Query(None, description="租户 ID（None=default）"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    items = await list_watchlists(session, tenant_id=tenant_id)
    return {"items": items, "count": len(items), "tenant_id": tenant_id}


@router.post("", status_code=201)
async def create(
    body: CreateWatchlistRequest,
    tenant_id: str | None = Query(None),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        w = await create_watchlist(
            session, name=body.name, description=body.description, tenant_id=tenant_id,
        )
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"watchlist name={body.name} already exists for tenant={tenant_id}",
        ) from exc
    return w


@router.get("/{watchlist_id}")
async def read(
    watchlist_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    w = await get_watchlist(session, watchlist_id=watchlist_id)
    if w is None:
        raise HTTPException(status_code=404, detail=f"watchlist_id={watchlist_id} not found")
    return w


@router.post("/{watchlist_id}/items", status_code=201)
async def add(
    body: AddItemRequest,
    watchlist_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    item = await add_item(
        session, watchlist_id=watchlist_id, symbol=body.symbol, note=body.note,
    )
    if item is None:
        raise HTTPException(
            status_code=404, detail=f"watchlist_id={watchlist_id} not found",
        )
    return item


@router.delete("/{watchlist_id}/items/{symbol}")
async def remove(
    watchlist_id: int = Path(..., ge=1),
    symbol: str = Path(..., min_length=1, max_length=32),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ok = await remove_item(
        session, watchlist_id=watchlist_id, symbol=symbol,
    )
    return {"watchlist_id": watchlist_id, "symbol": symbol, "removed": ok}


@router.delete("/{watchlist_id}")
async def delete(
    watchlist_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    ok = await delete_watchlist(session, watchlist_id=watchlist_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"watchlist_id={watchlist_id} not found",
        )
    return {"watchlist_id": watchlist_id, "deleted": True}
