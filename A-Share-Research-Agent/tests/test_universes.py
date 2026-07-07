"""universes 单测 — P2.6 股票池 preset 解析 + 缓存 + 错误传播。

覆盖：
- list_universes() — 5 个 index preset + all_a
- is_valid() — 白名单校验
- clear_cache() — 缓存清空
- resolve_universe() 缓存命中
- resolve_universe() 真解析（mock router 返 fake df）
- resolve_universe() 异步 / DataRouter 抛错转 UniverseError
- ALL_A 池抽样（sample_n）
- 未知 preset 抛 UniverseError
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from data_pipeline.universes import (
    UniverseError,
    clear_cache,
    is_valid,
    list_universes,
    resolve_universe,
)


@pytest.fixture(autouse=True)
def _flush_cache() -> None:
    """每个测试前清缓存，避免状态污染。"""
    clear_cache()
    yield
    clear_cache()


# ── 静态接口 ─────────────────────────────────────────

def test_list_universes_returns_six() -> None:
    """5 个 index preset + all_a。"""
    items = list_universes()
    names = {it["preset"] for it in items}
    assert names == {"hs300", "csi500", "csi1000", "gem", "star", "all_a"}


def test_list_universes_metadata() -> None:
    """每个 preset 含 code/exchange/name。"""
    items = list_universes()
    hs300 = next(it for it in items if it["preset"] == "hs300")
    assert hs300["code"] == "000300"
    assert hs300["exchange"] == "SH"
    assert "沪深300" in hs300["name"]


def test_is_valid_known_presets() -> None:
    """5 个 index + all_a 均合法。"""
    for p in ("hs300", "csi500", "csi1000", "gem", "star", "all_a"):
        assert is_valid(p), p


def test_is_valid_unknown() -> None:
    assert not is_valid("foo_bar")
    assert not is_valid("")


# ── resolve_universe 错误路径 ─────────────────────────

@pytest.mark.asyncio
async def test_resolve_unknown_preset_raises() -> None:
    with pytest.raises(UniverseError, match="未知 universe"):
        await resolve_universe("not_a_preset")


# ── resolve_universe async + 真解析 ───────────────────

@pytest.mark.asyncio
async def test_resolve_index_universe_returns_sorted_codes() -> None:
    """mock router 返 fake df → 解析为 6 位代码列表。"""
    fake_df = pd.DataFrame(
        {
            "品种代码": ["600519", "000001", "300750"],
            "品种名称": ["茅台", "平安", "宁德"],
            "纳入日期": ["2005-01-01"] * 3,
        }
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)

    codes = await resolve_universe("hs300", router=fake_router)
    assert codes == ["000001", "300750", "600519"]
    assert len(fake_router.route.await_args_list) == 1
    # 调的是 get_index_constituents，symbol=000300
    ca = fake_router.route.call_args
    assert ca.args[0] == "get_index_constituents"
    assert ca.kwargs == {"symbol": "000300"}


@pytest.mark.asyncio
async def test_resolve_index_universe_error_wrapped() -> None:
    """DataRouter 抛错时包成 UniverseError，便于上层统一捕获。"""
    fake_router = MagicMock()
    fake_router.route = AsyncMock(side_effect=RuntimeError("router down"))
    with pytest.raises(UniverseError, match="router down"):
        await resolve_universe("hs300", router=fake_router)


@pytest.mark.asyncio
async def test_resolve_index_universe_empty_raises() -> None:
    """router 返空 DataFrame → UniverseError。"""
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=pd.DataFrame())
    with pytest.raises(UniverseError, match="拉数据为空"):
        await resolve_universe("hs300", router=fake_router)


@pytest.mark.asyncio
async def test_resolve_index_universe_wrong_columns() -> None:
    """找不到代码列 → UniverseError。"""
    fake_router = MagicMock()
    fake_router.route = AsyncMock(
        return_value=pd.DataFrame({"foo": [1, 2, 3], "bar": ["a", "b", "c"]}),
    )
    with pytest.raises(UniverseError, match="找不到代码列"):
        await resolve_universe("hs300", router=fake_router)


# ── 缓存行为 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_cache_hit_avoids_router_call() -> None:
    """第二次调用走缓存，不再调 router。"""
    fake_df = pd.DataFrame({"品种代码": ["600519"], "品种名称": ["茅台"], "纳入日期": ["2020-01-01"]})
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)

    # 第一次：cache miss → 调 router
    codes1 = await resolve_universe("hs300", router=fake_router)
    assert codes1 == ["600519"]
    assert len(fake_router.route.await_args_list) == 1

    # 第二次：cache hit → 不再调 router
    codes2 = await resolve_universe("hs300", router=fake_router)
    assert codes2 == ["600519"]
    assert len(fake_router.route.await_args_list) == 1  # 没变


@pytest.mark.asyncio
async def test_clear_cache_forces_refetch() -> None:
    """clear_cache 后再调必重拉。"""
    fake_df = pd.DataFrame({"品种代码": ["600519"], "品种名称": ["茅台"], "纳入日期": ["2020-01-01"]})
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)

    await resolve_universe("hs300", router=fake_router)
    assert len(fake_router.route.await_args_list) == 1

    clear_cache()
    await resolve_universe("hs300", router=fake_router)
    assert len(fake_router.route.await_args_list) == 2


@pytest.mark.asyncio
async def test_use_cache_false_skips_cache() -> None:
    """use_cache=False 永远走 router。"""
    fake_df = pd.DataFrame({"品种代码": ["600519"], "品种名称": ["茅台"], "纳入日期": ["2020-01-01"]})
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)

    await resolve_universe("hs300", router=fake_router, use_cache=False)
    await resolve_universe("hs300", router=fake_router, use_cache=False)
    assert len(fake_router.route.await_args_list) == 2


# ── ALL_A 池 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_all_a_filters_invalid_codes() -> None:
    """ALL_A 池：6 位数字才保留，去重排序。"""
    fake_df = pd.DataFrame(
        {
            "代码": ["600519", "000001", "830001", "abcdef", "300750"],
            "名称": ["x"] * 5,
        }
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)

    codes = await resolve_universe("all_a", router=fake_router)
    # 5 位 + 非数字 + 6 位数字 过滤后剩 600519/000001/830001/300750
    assert codes == ["000001", "300750", "600519", "830001"]


@pytest.mark.asyncio
async def test_resolve_all_a_sample_n() -> None:
    """ALL_A 池抽样：固定种子 → 稳定结果。"""
    fake_df = pd.DataFrame(
        {"代码": [f"{i:06d}" for i in range(1, 1001)], "名称": ["x"] * 1000}
    )
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=fake_df)

    sample1 = await resolve_universe("all_a", router=fake_router, sample_n=50)
    sample2 = await resolve_universe("all_a", router=fake_router, sample_n=50)
    # 两次抽样（缓存后）结果一致（固定 seed=42）
    assert sample1 == sample2
    assert len(sample1) == 50
    # 全 6 位
    assert all(len(c) == 6 for c in sample1)


@pytest.mark.asyncio
async def test_resolve_all_a_empty_raises() -> None:
    fake_router = MagicMock()
    fake_router.route = AsyncMock(return_value=pd.DataFrame())
    with pytest.raises(UniverseError, match="全 A 股票池"):
        await resolve_universe("all_a", router=fake_router)
