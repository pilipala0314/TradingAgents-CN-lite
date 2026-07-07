"""CacheBackend 单元测试 — Protocol / InMemoryCache / TTL / 容量淘汰。"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from data_pipeline.cache import CacheBackend, CacheStats, InMemoryCache

# ── InMemoryCache 行为 ─────────────────────────────────────────────────

def test_in_memory_cache_basic_set_get() -> None:
    c = InMemoryCache()
    c.set("k", "v", ttl=10)
    assert c.get("k") == "v"


def test_in_memory_cache_miss_returns_none() -> None:
    c = InMemoryCache()
    assert c.get("nope") is None


def test_in_memory_cache_ttl_expiry() -> None:
    c = InMemoryCache()
    c.set("k", "v", ttl=0.1)
    assert c.get("k") == "v"
    time.sleep(0.15)
    assert c.get("k") is None  # 过期


def test_in_memory_cache_ttl_zero_means_no_expiry() -> None:
    c = InMemoryCache()
    c.set("k", "v", ttl=0)
    time.sleep(0.1)
    assert c.get("k") == "v"


def test_in_memory_cache_delete() -> None:
    c = InMemoryCache()
    c.set("k", "v", ttl=10)
    c.delete("k")
    assert c.get("k") is None


def test_in_memory_cache_clear() -> None:
    c = InMemoryCache()
    c.set("a", 1, ttl=10)
    c.set("b", 2, ttl=10)
    c.clear()
    assert c.get("a") is None
    assert c.get("b") is None
    assert c.stats().sets == 0


def test_in_memory_cache_stats_hit_miss() -> None:
    c = InMemoryCache()
    c.set("k", "v", ttl=10)
    c.get("k")  # hit
    c.get("k")  # hit
    c.get("nope")  # miss
    s = c.stats()
    assert s.hits == 2
    assert s.misses == 1
    assert s.hit_rate == pytest.approx(2 / 3)


def test_in_memory_cache_lru_eviction() -> None:
    c = InMemoryCache(max_items=3)
    c.set("a", 1, ttl=10)
    c.set("b", 2, ttl=10)
    c.set("c", 3, ttl=10)
    # 触发 a 被淘汰
    c.set("d", 4, ttl=10)
    assert c.get("a") is None
    assert c.get("b") == 2


def test_in_memory_cache_concurrent_safe() -> None:
    """多协程并发 set / get 不崩。"""
    c = InMemoryCache()

    async def writer(i: int) -> None:
        c.set(f"k{i}", i, ttl=10)

    async def reader(i: int) -> int | None:
        return c.get(f"k{i}")

    async def run() -> None:
        await asyncio.gather(*[writer(i) for i in range(100)])
        results = await asyncio.gather(*[reader(i) for i in range(100)])
        assert all(r == i for i, r in enumerate(results))

    asyncio.run(run())


# ── Protocol 结构性类型 ───────────────────────────────────────────────

def test_in_memory_cache_satisfies_protocol() -> None:
    """InMemoryCache 满足 CacheBackend Protocol（runtime_checkable）。"""
    c = InMemoryCache()
    assert isinstance(c, CacheBackend)


# ── DataRouter 注入 cache ──────────────────────────────────────────────

def test_router_default_cache_is_in_memory() -> None:
    from data_pipeline.data_router import DataRouter
    router = DataRouter()
    assert isinstance(router._cache, InMemoryCache)


def test_router_inject_custom_cache() -> None:
    """外部可注入自定义 CacheBackend。"""
    from data_pipeline.data_router import DataRouter

    class _SpyCache:
        def __init__(self) -> None:
            self.store: dict[str, Any] = {}
            self._stats = CacheStats()

        def get(self, key):
            self._stats.misses += 1
            return self.store.get(key)

        def set(self, key, value, ttl=0.0):
            self.store[key] = value
            self._stats.sets += 1

        def delete(self, key):
            self.store.pop(key, None)

        def clear(self):
            self.store.clear()
            self._stats = CacheStats()

        def stats(self):
            return self._stats

    from data_pipeline.providers.a_stock_data_provider import AStockDataProvider
    spy = _SpyCache()
    router = DataRouter(
        providers={"a_stock_data": AStockDataProvider()},
        method_table={"get_kline": {"a_stock_data": "get_kline"}},
        cache=spy,
    )
    assert router._cache is spy
    # health 应暴露 cache stats
    h = router.health()
    assert "cache" in h
    assert h["cache"]["sets"] == 0
