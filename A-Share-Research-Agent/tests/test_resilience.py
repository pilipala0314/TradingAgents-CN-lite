"""韧性层单元测试 — Retry / RateLimit / Circuit / IpPool / Bulkhead / Policy。"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from data_pipeline.resilience import (
    Bulkhead,
    BulkheadConfig,
    CffiImpersonateIpPool,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    IpPoolConfig,
    IpSelection,
    ProxyEnvIpPool,
    RateLimitConfig,
    RateLimiter,
    ResilienceError,
    ResiliencePolicy,
    RetryConfig,
    RetryPolicy,
    policy_from_dict,
)

# ── RetryPolicy ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_retry_success_first_try() -> None:
    """首次就成功 → 不重试。"""
    policy = RetryPolicy(RetryConfig(max_attempts=3, initial_delay_sec=0.01))
    calls = []

    async def fn() -> str:
        calls.append(1)
        return "ok"

    assert await policy.execute(fn) == "ok"
    assert len(calls) == 1
    assert policy.stats.success == 1
    assert policy.stats.retries == 0


@pytest.mark.asyncio
async def test_retry_eventual_success() -> None:
    """前 2 次失败，第 3 次成功。"""
    policy = RetryPolicy(RetryConfig(max_attempts=3, initial_delay_sec=0.01, jitter=False))
    calls = []

    async def fn() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise ValueError(f"fail-{len(calls)}")
        return "ok"

    assert await policy.execute(fn) == "ok"
    assert len(calls) == 3
    assert policy.stats.retries == 2
    assert policy.stats.success == 1


@pytest.mark.asyncio
async def test_retry_exhausted_raises_resilience_error() -> None:
    """3 次都失败 → ResilienceError(retry)。"""
    policy = RetryPolicy(RetryConfig(max_attempts=3, initial_delay_sec=0.01))

    async def fn() -> None:
        raise ValueError("always fail")

    with pytest.raises(ResilienceError) as exc_info:
        await policy.execute(fn)
    assert exc_info.value.strategy == "retry"
    assert exc_info.value.attempts == 3
    assert isinstance(exc_info.value.last_error, ValueError)


@pytest.mark.asyncio
async def test_retry_filter_exception_type() -> None:
    """retry_on 白名单外的异常 → 不重试。"""
    policy = RetryPolicy(
        RetryConfig(max_attempts=5, initial_delay_sec=0.01, retry_on=(ValueError,))
    )
    calls = []

    async def fn() -> None:
        calls.append(1)
        raise TypeError("not retried")

    with pytest.raises(TypeError):
        await policy.execute(fn)
    assert len(calls) == 1  # 不重试


@pytest.mark.asyncio
async def test_retry_backoff_progression() -> None:
    """delay 递增（无 jitter 验证退避序列）。"""
    policy = RetryPolicy(
        RetryConfig(
            max_attempts=4,
            initial_delay_sec=0.05,
            backoff_factor=2.0,
            jitter=False,
        )
    )
    delays = [policy._compute_delay(i) for i in range(1, 4)]
    assert delays == [0.05, 0.1, 0.2]


# ── RateLimiter ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_initial_burst() -> None:
    """前 N 个请求（≤burst）立即通过。"""
    limiter = RateLimiter(RateLimitConfig(rps=1.0, burst=3))
    start = time.monotonic()
    for _ in range(3):
        await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed < 0.05  # 几乎瞬时


@pytest.mark.asyncio
async def test_rate_limit_throttles() -> None:
    """超出 burst 后要等待。"""
    limiter = RateLimiter(RateLimitConfig(rps=10.0, burst=2))
    # 吃掉 burst
    await limiter.acquire()
    await limiter.acquire()
    # 第 3 个要等 ~0.1s
    start = time.monotonic()
    await limiter.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # 至少等 1/rps


# ── CircuitBreaker ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_circuit_closed_to_open() -> None:
    """连续 N 次失败 → OPEN。"""
    cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3, recovery_timeout_sec=1.0))

    async def fail() -> None:
        raise ValueError("fail")

    for _ in range(3):
        with pytest.raises(ValueError):
            await cb.execute(fail)
    assert cb.state is CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_open_rejects_immediately() -> None:
    """OPEN 状态直接抛 ResilienceError，不调 fn。"""
    cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout_sec=10.0))

    async def fail() -> None:
        raise ValueError("fail")

    with pytest.raises(ValueError):
        await cb.execute(fail)
    assert cb.state is CircuitState.OPEN

    calls = []

    async def any_call() -> None:
        calls.append(1)

    with pytest.raises(ResilienceError) as exc_info:
        await cb.execute(any_call)
    assert exc_info.value.strategy == "circuit"
    assert len(calls) == 0  # 没调用


@pytest.mark.asyncio
async def test_circuit_open_to_half_open_to_closed() -> None:
    """OPEN → 等 recovery → HALF_OPEN → 试水成功 → CLOSED。"""
    cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout_sec=0.1))

    async def fail() -> None:
        raise ValueError("fail")

    async def ok() -> str:
        return "ok"

    with pytest.raises(ValueError):
        await cb.execute(fail)
    assert cb.state is CircuitState.OPEN

    # 等恢复期
    await asyncio.sleep(0.15)
    assert cb.state is CircuitState.HALF_OPEN

    # 试水成功
    assert await cb.execute(ok) == "ok"
    assert cb.state is CircuitState.CLOSED


# ── IpPool ───────────────────────────────────────────────────────────

def test_proxy_env_pool_empty() -> None:
    """无环境变量 → 直连。"""
    for k in ("IP_POOL_LIST", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(k, None)
    pool = ProxyEnvIpPool()
    ip = pool.select()
    assert ip.url == ""
    assert ip.label == "direct"


def test_proxy_env_pool_round_robin(monkeypatch) -> None:
    monkeypatch.setenv("IP_POOL_LIST", "http://p1:7890,http://p2:7890,http://p3:7890")
    pool = ProxyEnvIpPool(IpPoolConfig(strategy="round_robin"))
    seen = [pool.select().url for _ in range(6)]
    assert seen[0] != seen[1] != seen[2]
    assert seen[3] == seen[0]  # 循环


def test_proxy_env_pool_random(monkeypatch) -> None:
    monkeypatch.setenv("IP_POOL_LIST", "http://p1,http://p2,http://p3")
    pool = ProxyEnvIpPool(IpPoolConfig(strategy="random"))
    seen = {pool.select().url for _ in range(20)}
    assert len(seen) > 1  # 随机分布


def test_proxy_env_pool_mark_failure_increments() -> None:
    pool = ProxyEnvIpPool()
    pool.mark_failure(IpSelection(url="x", label="x"))
    s = pool.stats()
    assert s["ip_switches"] == 1
    assert s["failed"] == 1


def test_cffi_impersonate_select() -> None:
    pool = CffiImpersonateIpPool(profiles=["chrome120", "safari15_3"])
    seen: set[str] = set()
    for _ in range(10):
        ip = pool.select()
        if pool.available:
            seen.add(ip.label)
        else:
            # curl_cffi 未装 → 回退到 direct（标 cffi-direct）
            seen.add(ip.label)
    # 至少用到一个 label
    assert seen
    # 如果 available，至少一个是 cffi-* 开头
    if pool.available:
        assert any(label.startswith("cffi-") for label in seen)


def test_cffi_impersonate_unavailable_falls_back() -> None:
    """curl_cffi 未装时回退到 direct（优雅降级）。"""
    pool = CffiImpersonateIpPool(profiles=["chrome120"])
    if not pool.available:
        ip = pool.select()
        assert ip.label == "direct"
        assert ip.impersonate is None


# ── Bulkhead ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bulkhead_concurrent_limit() -> None:
    """同时最多 N 个，多余的 await。"""
    bh = Bulkhead(BulkheadConfig(max_concurrent=2))
    active = 0
    peak = 0

    async def work() -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return "done"

    results = await asyncio.gather(*[bh.execute(work) for _ in range(5)])
    assert all(r == "done" for r in results)
    assert peak <= 2


# ── ResiliencePolicy 编排器 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_policy_chains_retry_and_circuit() -> None:
    """policy = retry + circuit：circuit 触发后直接 reject。

    期望：第 1 次调用 → retry 用尽 → circuit 累计 1 次失败 → OPEN。
    第 2 次调用 → circuit OPEN → 直接抛 circuit 错。
    """
    policy = ResiliencePolicy(
        retry=RetryConfig(max_attempts=2, initial_delay_sec=0.01),
        circuit=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_sec=10.0),
    )
    calls = []

    async def fail() -> None:
        calls.append(1)
        raise ValueError("x")

    # 第 1 次: retry 内 2 次失败 → retry 抛错（被 circuit 包住）
    with pytest.raises(ResilienceError):
        await policy.execute(fail)
    # circuit 累计 1 次 failure → OPEN

    # 第 2 次: circuit OPEN → 直接 reject
    with pytest.raises(ResilienceError) as exc_info:
        await policy.execute(fail)
    assert exc_info.value.strategy == "circuit"


@pytest.mark.asyncio
async def test_policy_disabled_strategy_is_skipped() -> None:
    """不传的策略自动跳过（优雅 disable）。"""
    policy = ResiliencePolicy(retry=RetryConfig(max_attempts=2, initial_delay_sec=0.01))
    calls = []

    async def fail() -> None:
        calls.append(1)
        raise ValueError("x")

    with pytest.raises(ResilienceError):
        await policy.execute(fail)
    # 只跑 2 次（首次 + 1 次重试）
    assert len(calls) == 2
    # 其它策略 stats 全部 0
    assert policy.stats() == {"retry": policy.stats()["retry"]}


@pytest.mark.asyncio
async def test_policy_stats_aggregates_all_strategies() -> None:
    """policy.stats() 聚合所有子策略。"""
    policy = ResiliencePolicy(
        retry=RetryConfig(max_attempts=2, initial_delay_sec=0.01),
        rate_limit=RateLimitConfig(rps=100, burst=10),
        bulkhead=BulkheadConfig(max_concurrent=5),
    )

    async def ok() -> str:
        return "ok"

    await policy.execute(ok)
    s = policy.stats()
    assert "retry" in s
    assert "rate_limit" in s
    assert "bulkhead" in s
    assert "circuit" not in s  # 没配置就 skip
    assert "ip_pool" not in s


def test_policy_from_dict_basic() -> None:
    """从 dict 构造：常用入口。"""
    p = policy_from_dict({
        "retry": {"max_attempts": 5, "initial_delay_sec": 1.0},
        "rate_limit": {"rps": 2.0, "burst": 5},
        "circuit": {"failure_threshold": 10},
        "bulkhead": {"max_concurrent": 3},
        "ip_pool": "cffi_impersonate",
    })
    assert p.retry is not None
    assert p.retry.config.max_attempts == 5
    assert p.rate_limit is not None
    assert p.rate_limit.config.rps == 2.0
    assert p.circuit is not None
    assert p.circuit.config.failure_threshold == 10
    assert p.bulkhead is not None
    assert p.bulkhead.config.max_concurrent == 3
    assert isinstance(p.ip_pool, CffiImpersonateIpPool)


def test_policy_from_dict_empty_means_default() -> None:
    """None/空 dict → 全部 disable 的最小 policy。"""
    p = policy_from_dict(None)
    assert p.retry is None
    assert p.rate_limit is None
    assert p.circuit is None
    assert p.bulkhead is None
    assert p.ip_pool is None
