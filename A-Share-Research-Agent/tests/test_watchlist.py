"""P1-T1 自选股 list CRUD 单测。

覆盖（service 层 + HTTP 端点）：

service 层：
- list_watchlists 隔离 tenant
- create_watchlist → 重复 name 同 tenant 抛 IntegrityError
- add_item 幂等（重复 symbol 不抛错）
- add_item 不存在的 watchlist_id → None
- remove_item 幂等
- get_watchlist 返 items 列表
- delete_watchlist 级联

HTTP 端点（patch get_db）：
- GET /api/watchlist
- POST /api/watchlist 201 / 409
- GET /api/watchlist/{id} 200 / 404
- POST /api/watchlist/{id}/items 201 / 404
- DELETE /api/watchlist/{id}/items/{symbol} 幂等
- DELETE /api/watchlist/{id} 200 / 404
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from services.watchlist_service import (
    add_item,
    create_watchlist,
    delete_watchlist,
    get_watchlist,
    list_watchlists,
    remove_item,
)


def _run(coro):
    return _get_loop().run_until_complete(coro)


def _get_loop():
    import asyncio
    return asyncio.new_event_loop()


# ── 1. service 层：list ─────────────────────────


def test_list_watchlists_returns_empty_for_unknown_tenant():
    """无 list → 空 list。"""
    session = AsyncMock()
    empty_scalars = MagicMock()
    empty_scalars.all.return_value = []
    session.execute.return_value = empty_scalars
    out = _run(list_watchlists(session, tenant_id="t1"))
    assert out == []


def test_list_watchlists_includes_item_count():
    """list_watchlists 返回的 dict 含 item_count。"""
    w = MagicMock()
    w.watchlist_id = 1
    w.watchlist_uuid = "uuid-1"
    w.name = "default"
    w.description = None
    w.created_at = None
    w.updated_at = None

    # 第 1 次 execute：select(DimWatchlist) → 1 行
    list_result = MagicMock()
    list_scalars = MagicMock()
    list_scalars.all.return_value = [w]
    list_result.scalars.return_value = list_scalars
    # 第 2 次 execute：count() for item_count
    count_result = MagicMock()
    count_result.scalar.return_value = 5

    session = AsyncMock()
    session.execute.side_effect = [list_result, count_result]

    out = _run(list_watchlists(session, tenant_id=None))
    assert len(out) == 1
    assert out[0]["name"] == "default"
    assert out[0]["item_count"] == 5


# ── 2. service 层：create ─────────────────────────


def test_create_watchlist_success():
    """create_watchlist 返回新 dict，item_count=0。"""
    session = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    async def _fake_refresh(obj):
        obj.watchlist_id = 10
        obj.watchlist_uuid = MagicMock()
        obj.watchlist_uuid.__str__ = lambda self: "uuid-x"
        return obj

    session.refresh.side_effect = _fake_refresh
    out = _run(create_watchlist(session, name="default", tenant_id="t1"))
    assert out["name"] == "default"
    assert out["item_count"] == 0
    assert out["watchlist_uuid"] == "uuid-x"


def test_create_watchlist_duplicate_raises_integrity_error():
    """同 (tenant, name) 重复 → IntegrityError 透传。"""
    session = AsyncMock()
    session.commit = AsyncMock(side_effect=IntegrityError("dup", None, None))
    session.refresh = AsyncMock()
    with pytest.raises(IntegrityError):
        _run(create_watchlist(session, name="default", tenant_id="t1"))


# ── 3. service 层：get_watchlist ─────────────────────────


def test_get_watchlist_not_found():
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    assert _run(get_watchlist(session, watchlist_id=999)) is None


def test_get_watchlist_returns_items():
    w = MagicMock()
    w.watchlist_id = 1
    w.watchlist_uuid = "uuid-1"
    w.name = "default"
    w.description = "desc"
    item = MagicMock()
    item.item_id = 100
    item.watchlist_id = 1
    item.symbol = "600519"
    item.note = "maotai"
    item.added_at = None

    session = AsyncMock()
    session.get = AsyncMock(return_value=w)
    list_result = MagicMock()
    list_scalars = MagicMock()
    list_scalars.all.return_value = [item]
    list_result.scalars.return_value = list_scalars
    session.execute.return_value = list_result
    out = _run(get_watchlist(session, watchlist_id=1))
    assert out is not None
    assert out["name"] == "default"
    assert out["item_count"] == 1
    assert out["items"][0]["symbol"] == "600519"


# ── 4. service 层：add_item ─────────────────────────


def test_add_item_success():
    w = MagicMock()
    w.watchlist_id = 1

    new_item = MagicMock()
    new_item.item_id = 11
    new_item.watchlist_id = 1
    new_item.symbol = "600519"
    new_item.note = None
    new_item.added_at = None

    session = AsyncMock()
    session.get = AsyncMock(return_value=w)
    session.commit = AsyncMock()

    async def _fake_refresh(obj):
        obj.item_id = 11
        obj.symbol = "600519"
        obj.watchlist_id = 1
        obj.note = None
        obj.added_at = None
        return obj

    session.refresh = AsyncMock(side_effect=_fake_refresh)
    out = _run(add_item(session, watchlist_id=1, symbol="600519"))
    assert out is not None
    assert out["symbol"] == "600519"


def test_add_item_watchlist_not_found():
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    assert _run(add_item(session, watchlist_id=999, symbol="600519")) is None


def test_add_item_idempotent():
    """重复 add 同一 symbol → 不抛错，返回已有 item。"""
    w = MagicMock()
    w.watchlist_id = 1

    existing = MagicMock()
    existing.item_id = 11
    existing.watchlist_id = 1
    existing.symbol = "600519"
    existing.note = None
    existing.added_at = None

    session = AsyncMock()
    session.get = AsyncMock(return_value=w)
    session.commit = AsyncMock(side_effect=IntegrityError("dup", None, None))
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()

    # 第二次 execute 查已有 item
    scalars = MagicMock()
    scalars.scalar_one_or_none.return_value = existing
    session.execute.return_value = scalars

    out = _run(add_item(session, watchlist_id=1, symbol="600519"))
    assert out is not None
    assert out["item_id"] == 11
    assert out["symbol"] == "600519"


# ── 5. service 层：remove_item ─────────────────────────


def test_remove_item_returns_rowcount_bool():
    """remove_item 根据 rowcount > 0 返 True / False。"""
    session = AsyncMock()

    # 场景 1：删除了 1 条
    result = MagicMock()
    result.rowcount = 1
    session.execute.return_value = result
    session.commit = AsyncMock()
    assert _run(remove_item(session, watchlist_id=1, symbol="600519")) is True

    # 场景 2：没删到
    result.rowcount = 0
    assert _run(remove_item(session, watchlist_id=1, symbol="xxxxx")) is False


# ── 6. service 层：delete_watchlist ─────────────────────────


def test_delete_watchlist_not_found():
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    assert _run(delete_watchlist(session, watchlist_id=999)) is False


def test_delete_watchlist_cascades():
    w = MagicMock()
    session = AsyncMock()
    session.get = AsyncMock(return_value=w)
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    assert _run(delete_watchlist(session, watchlist_id=1)) is True


# ── 7. HTTP 端点：mock get_db ─────────────────────────


@pytest.mark.asyncio
async def test_http_list_watchlists():
    """GET /api/watchlist 200 + 含 items/count。"""
    with patch("apps.api.routers.watchlist.list_watchlists",
               AsyncMock(return_value=[{"watchlist_id": 1, "name": "default", "item_count": 3}])):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.get("/api/watchlist")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert data["items"][0]["name"] == "default"


@pytest.mark.asyncio
async def test_http_create_201():
    with patch("apps.api.routers.watchlist.create_watchlist",
               AsyncMock(return_value={"watchlist_id": 5, "name": "new", "item_count": 0})):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.post("/api/watchlist?tenant_id=t1", json={"name": "new"})
    assert r.status_code == 201
    assert r.json()["name"] == "new"


@pytest.mark.asyncio
async def test_http_create_409_on_duplicate():
    with patch("apps.api.routers.watchlist.create_watchlist",
               AsyncMock(side_effect=IntegrityError("dup", None, None))):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.post("/api/watchlist", json={"name": "x"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_http_get_404():
    with patch("apps.api.routers.watchlist.get_watchlist",
               AsyncMock(return_value=None)):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.get("/api/watchlist/999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_http_add_item_201():
    with patch("apps.api.routers.watchlist.add_item",
               AsyncMock(return_value={"item_id": 1, "symbol": "600519", "watchlist_id": 1})):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.post("/api/watchlist/1/items", json={"symbol": "600519"})
    assert r.status_code == 201
    assert r.json()["symbol"] == "600519"


@pytest.mark.asyncio
async def test_http_add_item_404():
    with patch("apps.api.routers.watchlist.add_item",
               AsyncMock(return_value=None)):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.post("/api/watchlist/999/items", json={"symbol": "600519"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_http_remove_item_idempotent():
    with patch("apps.api.routers.watchlist.remove_item", AsyncMock(return_value=False)):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.delete("/api/watchlist/1/items/xxxxx")
    assert r.status_code == 200
    assert r.json()["removed"] is False


@pytest.mark.asyncio
async def test_http_delete_200():
    with patch("apps.api.routers.watchlist.delete_watchlist", AsyncMock(return_value=True)):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.delete("/api/watchlist/1")
    assert r.status_code == 200
    assert r.json()["deleted"] is True


@pytest.mark.asyncio
async def test_http_delete_404():
    with patch("apps.api.routers.watchlist.delete_watchlist", AsyncMock(return_value=False)):
        async with AsyncClient(
            transport=ASGITransport(app=_import_app()), base_url="http://t"
        ) as c:
            r = await c.delete("/api/watchlist/999")
    assert r.status_code == 404


# ── helpers ─────────────────────────


def _import_app():
    """延迟导入避免 lifespan 副作用。"""
    from apps.api.main import app
    return app
