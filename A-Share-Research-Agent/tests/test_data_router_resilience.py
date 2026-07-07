"""DataRouter 韧性策略集成测试。"""
from __future__ import annotations

import asyncio
import time

import pytest

from data_pipeline.data_router import DataFetchError, DataRouter
from data_pipeline.resilience import (
    CircuitBreakerConfig,
    CircuitState,
    ResilienceError,
    ResiliencePolicy,
    RetryConfig,
)


class _FakeProvider:
    """可控制成败次数的 fake provider。

    通过 `always_fail` 字段控制本实例是否必失败（与参数 n_fail 互不影响）。
    """

    def __init__(self, always_fail: bool = False) -> None:
        self.always_fail = always_fail
        self.calls: list[tuple] = []

    async def get_a(self, value: str = "x") -> str:
        self.calls.append(("a", value))
        return f"a-{value}"

    async def get_b_fail(self, n_fail: int = 0) -> str:
        self.calls.append(("b", n_fail))
        if self.always_fail:
            raise ValueError(f"fail-{n_fail}")
        return "b-ok"


# ── set_resilience / get_resilience 基础 ──────────────────────────────

def test_set_resilience_stores_policy() -> None:
    provider = _FakeProvider()
    router = DataRouter(providers={"fake": provider}, method_table={"get_a": {"fake": "get_a"}})
    policy = ResiliencePolicy(retry=RetryConfig(max_attempts=2))
    router.set_resilience("get_a", "fake", policy)
    assert router.get_resilience("get_a", "fake") is policy


def test_set_resilience_none_removes() -> None:
    provider = _FakeProvider()
    router = DataRouter(providers={"fake": provider}, method_table={"get_a": {"fake": "get_a"}})
    policy = ResiliencePolicy(retry=RetryConfig(max_attempts=2))
    router.set_resilience("get_a", "fake", policy)
    router.set_resilience("get_a", "fake", None)
    assert router.get_resilience("get_a", "fake") is None


def test_default_resilience_applies_to_all_endpoints() -> None:
    provider = _FakeProvider()
    default = ResiliencePolicy(retry=RetryConfig(max_attempts=2))
    router = DataRouter(
        providers={"fake": provider},
        method_table={"get_a": {"fake": "get_a"}, "get_b_fail": {"fake": "get_b_fail"}},
        default_resilience=default,
    )
    # 没显式 set_resilience → 用 default
    assert router.get_resilience("get_a", "fake") is default
    assert router.get_resilience("get_b_fail", "fake") is default


def test_specific_resilience_overrides_default() -> None:
    provider = _FakeProvider()
    default = ResiliencePolicy(retry=RetryConfig(max_attempts=1))
    specific = ResiliencePolicy(retry=RetryConfig(max_attempts=5))
    router = DataRouter(
        providers={"fake": provider},
        method_table={"get_a": {"fake": "get_a"}, "get_b_fail": {"fake": "get_b_fail"}},
        default_resilience=default,
    )
    router.set_resilience("get_a", "fake", specific)
    assert router.get_resilience("get_a", "fake") is specific
    assert router.get_resilience("get_b_fail", "fake") is default  # 其它仍走 default


# ── route() 集成 retry ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_route_with_retry_recovers() -> None:
    """retry 内调 2 次（验证 retry 与 route() 集成）。"""
    provider = _FakeProvider(always_fail=True)
    router = DataRouter(
        providers={"fake": provider},
        method_table={"get_b_fail": {"fake": "get_b_fail"}},
        default_strict=True,  # strict 模式：单一 provider 失败即抛
        cache_ttl=0,
    )
    router.set_resilience(
        "get_b_fail", "fake",
        ResiliencePolicy(retry=RetryConfig(max_attempts=2, initial_delay_sec=0.01)),
    )
    # 2 次都失败 → retry 用尽 → ResilienceError → fallback 链空 → DataFetchError
    with pytest.raises(DataFetchError):
        await router.route("get_b_fail", n_fail=1, use_cache=False)
    # retry 调了 2 次
    b_calls = sum(1 for c in provider.calls if c[0] == "b")
    assert b_calls == 2


@pytest.mark.asyncio
async def test_route_resilience_off_by_default() -> None:
    """默认不挂载 resilience → 行为与以前一致。"""
    provider = _FakeProvider()
    router = DataRouter(
        providers={"fake": provider},
        method_table={"get_a": {"fake": "get_a"}},
        cache_ttl=0,
    )
    result = await router.route("get_a", value="hello", use_cache=False)
    assert result == "a-hello"
    # 韧性 stats 为空
    assert router.resilience_stats() == {}


# ── 韧性失败走 fallback 链 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_resilience_failure_triggers_fallback() -> None:
    """首选 provider 韧性用尽 → 走 fallback provider。"""
    first = _FakeProvider(always_fail=True)
    second = _FakeProvider(always_fail=False)
    router = DataRouter(
        providers={"first": first, "second": second},
        method_table={"get_b_fail": {"first": "get_b_fail", "second": "get_b_fail"}},
        default_strict=False,
        cache_ttl=0,
    )
    # 给 first 配 retry（max=2），给 second 不配 → first 必失败，second 必成功
    router.set_resilience(
        "get_b_fail", "first",
        ResiliencePolicy(retry=RetryConfig(max_attempts=2, initial_delay_sec=0.01)),
    )
    result = await router.route("get_b_fail", n_fail=1, use_cache=False)
    assert result == "b-ok"
    first_calls = sum(1 for c in first.calls if c[0] == "b")
    second_calls = sum(1 for c in second.calls if c[0] == "b")
    assert first_calls == 2  # retry 内 2 次
    assert second_calls == 1  # 兜底 1 次
    h = router.health()
    by_prov = {(e["method"], e["provider"]): e for e in h["endpoints"]}
    assert by_prov[("get_b_fail", "first")]["fallback_count"] == 0
    assert by_prov[("get_b_fail", "second")]["fallback_count"] == 1


# ── resilience_stats 暴露 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_resilience_stats_aggregated_in_router() -> None:
    """resilience stats 通过 router.resilience_stats() 暴露。"""
    provider = _FakeProvider()
    router = DataRouter(
        providers={"fake": provider},
        method_table={"get_a": {"fake": "get_a"}},
        cache_ttl=0,
    )
    policy = ResiliencePolicy(
        retry=RetryConfig(max_attempts=2, initial_delay_sec=0.01),
    )
    router.set_resilience("get_a", "fake", policy)
    await router.route("get_a", value="x", use_cache=False)
    s = router.resilience_stats()
    assert "get_a:fake" in s
    assert "retry" in s["get_a:fake"]
    assert s["get_a:fake"]["retry"]["success"] == 1


# ── 韧性开/关不破坏现有 fallback 行为 ───────────────────────────────

@pytest.mark.asyncio
async def test_no_resilience_keeps_old_fallback_behavior() -> None:
    """不配 resilience → 行为与原 P4/P5 测试一致。"""
    first = _FakeProvider(always_fail=True)
    second = _FakeProvider(always_fail=False)
    router = DataRouter(
        providers={"first": first, "second": second},
        method_table={"get_b_fail": {"first": "get_b_fail", "second": "get_b_fail"}},
        default_strict=False,
        cache_ttl=0,
    )
    # first 失败（无 retry），second 成功
    result = await router.route("get_b_fail", n_fail=1, use_cache=False)
    assert result == "b-ok"
    first_calls = sum(1 for c in first.calls if c[0] == "b")
    second_calls = sum(1 for c in second.calls if c[0] == "b")
    assert first_calls == 1
    assert second_calls == 1
