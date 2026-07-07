"""DataRouter 单元测试 — 路由策略 / cache / stats / strict / timeout。"""
from __future__ import annotations

import asyncio

import pytest

from data_pipeline.data_router import DataFetchError, DataRouter, EndpointStats

# ── 测试用 fake provider ──────────────────────────────────────────────

class _FakeProvider:
    """测试用 fake provider：只暴露测试关心的 2 个方法。"""

    def __init__(self) -> None:
        self.call_count: dict[str, int] = {}
        self.delay = 0.0

    async def get_a(self) -> str:
        self.call_count["get_a"] = self.call_count.get("get_a", 0) + 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return "a-ok"

    async def get_b(self, x: int) -> str:
        self.call_count["get_b"] = self.call_count.get("get_b", 0) + 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return f"b-ok-{x}"

    async def get_fail(self) -> str:
        self.call_count["get_fail"] = self.call_count.get("get_fail", 0) + 1
        raise RuntimeError("get_fail always fails")


# 极简 method_table — 测试路由逻辑用
_MINIMAL_METHOD_TABLE = {
    "get_a": {"fake": "get_a"},
    "get_b": {"fake": "get_b"},
    "get_fail": {"fake": "get_fail"},
}


def _build_test_router(providers: dict | None = None, **kwargs) -> DataRouter:
    """用极简 method_table 构建测试 router。"""
    return DataRouter(
        providers=providers or {"fake": _FakeProvider()},
        method_table=_MINIMAL_METHOD_TABLE,
        **kwargs,
    )


# ── 基础路由 ──────────────────────────────────────────────────────────

def test_router_constructor_with_providers() -> None:
    """providers 注入 + method_table 注册。"""
    router = _build_test_router()
    assert "get_a" in router.available_methods()
    assert "get_b" in router.available_methods()
    assert "fake" in router.providers


def test_router_constructor_with_akshare_client_compat() -> None:
    """老 AShareDataRouter(akshare_client=...) 构造签名兼容 — 内部注入 'akshare' key。"""
    fake = _FakeProvider()
    router = DataRouter(akshare_client=fake, method_table=_MINIMAL_METHOD_TABLE)
    # 老签名：akshare_client= 注入到 providers['akshare']
    assert "akshare" in router.providers


@pytest.mark.asyncio
async def test_router_route_basic_success() -> None:
    router = _build_test_router()
    fake = router.providers["fake"]
    result = await router.route("get_a")
    assert result == "a-ok"
    assert fake.call_count["get_a"] == 1


@pytest.mark.asyncio
async def test_router_route_with_args_kwargs() -> None:
    router = _build_test_router()
    result = await router.route("get_b", 42)
    assert result == "b-ok-42"


# ── Fallback chain ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_fallback_chain_skips_failed_provider() -> None:
    """fallback chain 中第一个 provider 失败 → 跳到下一个。"""
    first = _FakeProvider()
    second = _FakeProvider()
    router = DataRouter(
        providers={"first": first, "second": second},
        method_table={"get_fail": {"first": "get_fail", "second": "get_a"}},  # first 失败, second 走 get_a 成功
        default_strict=False,
    )
    result = await router.route("get_fail")
    assert result == "a-ok"
    assert first.call_count["get_fail"] == 1
    assert second.call_count["get_a"] == 1


@pytest.mark.asyncio
async def test_router_all_failed_raises() -> None:
    """所有 provider 失败 → 抛 DataFetchError。"""
    first = _FakeProvider()
    second = _FakeProvider()
    router = DataRouter(
        providers={"first": first, "second": second},
        method_table={"get_fail": {"first": "get_fail", "second": "get_fail"}},
        default_strict=False,
    )
    with pytest.raises(DataFetchError) as exc_info:
        await router.route("get_fail")
    assert exc_info.value.method == "get_fail"
    assert "first" in exc_info.value.attempted_providers
    assert "second" in exc_info.value.attempted_providers


# ── Strict 模式 ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_strict_mode_first_failure_raises() -> None:
    """strict=True：第一个 provider 失败就抛（不试后续）。"""
    first = _FakeProvider()
    second = _FakeProvider()
    router = DataRouter(
        providers={"first": first, "second": second},
        method_table={"get_a": {"first": "get_fail", "second": "get_a"}},  # first 走 get_fail (失败)
        default_strict=True,
    )
    with pytest.raises(DataFetchError):
        await router.route("get_a")
    # first 调用 1 次，second 不应被调用
    assert first.call_count["get_fail"] == 1
    assert second.call_count.get("get_a", 0) == 0


# ── TTL 缓存 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_cache_reuses_result() -> None:
    """同 method + 同参数在 cache_ttl 内复用，provider 不重复调用。"""
    router = _build_test_router(cache_ttl=5.0)
    fake = router.providers["fake"]
    r1 = await router.route("get_b", 99)
    r2 = await router.route("get_b", 99)
    assert r1 == r2 == "b-ok-99"
    # 缓存命中：fake 只调 1 次
    assert fake.call_count["get_b"] == 1


@pytest.mark.asyncio
async def test_router_cache_different_args_does_not_reuse() -> None:
    """不同参数的缓存 key 不同。"""
    router = _build_test_router(cache_ttl=5.0)
    fake = router.providers["fake"]
    r1 = await router.route("get_b", 1)
    r2 = await router.route("get_b", 2)
    assert r1 == "b-ok-1"
    assert r2 == "b-ok-2"
    assert fake.call_count["get_b"] == 2


@pytest.mark.asyncio
async def test_router_cache_disabled_when_use_cache_false() -> None:
    """use_cache=False 不命中缓存。"""
    router = _build_test_router(cache_ttl=5.0)
    fake = router.providers["fake"]
    await router.route("get_a", use_cache=False)
    await router.route("get_a", use_cache=False)
    assert fake.call_count["get_a"] == 2


# ── 超时 ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_timeout_raises() -> None:
    """provider 慢于 timeout → 抛错。"""
    fake = _FakeProvider()
    fake.delay = 0.5
    router = DataRouter(
        providers={"fake": fake},
        method_table={"get_a": {"fake": "get_a"}},
        default_timeout=0.1,
        default_strict=False,
    )
    with pytest.raises(DataFetchError):
        await router.route("get_a")
    # fake 调用 1 次（超时前已开始）
    assert fake.call_count["get_a"] == 1


# ── 端点健康度统计 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_health_reports_stats() -> None:
    """调用后 health() 反映 success / failure / latency。"""
    router = _build_test_router()
    await router.route("get_a")  # success
    with pytest.raises(DataFetchError):
        await router.route("get_fail")  # fail

    h = router.health()
    assert h["router"] == "DataRouter"
    assert h["calls_total"] == 2
    assert h["calls_success"] == 1
    assert h["calls_success_rate"] == 0.5

    # 找 get_a 和 get_fail 的端点
    by_method = {e["method"]: e for e in h["endpoints"]}
    assert by_method["get_a"]["total"] == 1
    assert by_method["get_a"]["success"] == 1
    assert by_method["get_a"]["success_rate"] == 1.0
    assert by_method["get_fail"]["total"] == 1
    assert by_method["get_fail"]["failure"] == 1
    assert "get_fail always fails" in by_method["get_fail"]["last_error"]


@pytest.mark.asyncio
async def test_router_health_resets() -> None:
    """reset_stats 后统计清零。"""
    router = _build_test_router()
    await router.route("get_a")
    assert router.health()["calls_total"] == 1
    router.reset_stats()
    assert router.health()["calls_total"] == 0


# ── EndpointStats dataclass ───────────────────────────────────────────

def test_endpoint_stats_to_dict() -> None:
    """EndpointStats.to_dict 字段完整。"""
    s = EndpointStats(method="m", provider="p")
    s.record(success=True, latency_ms=10.0)
    s.record(success=False, latency_ms=20.0, error="boom")
    d = s.to_dict()
    assert d["method"] == "m"
    assert d["provider"] == "p"
    assert d["total"] == 2
    assert d["success"] == 1
    assert d["failure"] == 1
    assert d["success_rate"] == 0.5
    assert d["last_error"] == "boom"
    # 新增字段
    assert d["fallback_count"] == 0
    assert d["fallback_rate"] == 0.0


def test_endpoint_stats_clears_stale_error_and_response_samples() -> None:
    """最后一次成功/失败应对应最后一次样本，不保留陈旧错误或成功输出。"""
    s = EndpointStats(method="m", provider="p")
    s.record(
        success=False,
        latency_ms=20.0,
        error="boom",
        error_sample={"error": "boom"},
    )
    assert s.last_error == "boom"
    assert s.error_sample == {"error": "boom"}

    s.record(
        success=True,
        latency_ms=10.0,
        response_sample={"rows": 2},
    )
    assert s.last_error == ""
    assert s.response_sample == {"rows": 2}
    assert s.error_sample == {}

    s.record(
        success=False,
        latency_ms=30.0,
        error="timeout",
        error_sample={"error": "timeout"},
    )
    assert s.last_error == "timeout"
    assert s.response_sample == {}
    assert s.error_sample == {"error": "timeout"}


# ── Fallback 统计（Phase 1） ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_count_increments_on_fallback() -> None:
    """首选 provider 失败 → fallback provider 被调用 → fallback_count += 1。"""
    first = _FakeProvider()
    second = _FakeProvider()
    router = DataRouter(
        providers={"first": first, "second": second},
        method_table={
            "get_a": {"first": "get_fail", "second": "get_a"},  # first 失败 → fallback 到 second
        },
        default_strict=False,
        cache_ttl=0,  # 关闭 cache，确保每次都打 provider
    )
    result = await router.route("get_a", use_cache=False)
    assert result == "a-ok"

    by_prov = {(e["method"], e["provider"]): e for e in router.health()["endpoints"]}
    # first 端点 (get_a:first via get_fail): 失败 1 次（非 fallback）
    first_stats = by_prov[("get_a", "first")]
    assert first_stats["total"] == 1
    assert first_stats["failure"] == 1
    assert first_stats["fallback_count"] == 0
    # second 端点 (get_a:second via get_a): 成功 1 次（fallback 触发）
    second_stats = by_prov[("get_a", "second")]
    assert second_stats["total"] == 1
    assert second_stats["success"] == 1
    assert second_stats["fallback_count"] == 1
    assert second_stats["fallback_rate"] == 1.0


@pytest.mark.asyncio
async def test_no_fallback_when_first_succeeds() -> None:
    """首选 provider 成功时，chain 中的其他 provider 不被调用。"""
    first = _FakeProvider()
    second = _FakeProvider()
    router = DataRouter(
        providers={"first": first, "second": second},
        method_table={"get_a": {"first": "get_a", "second": "get_a"}},
        default_strict=False,
    )
    await router.route("get_a")

    by_prov = {e["provider"]: e for e in router.health()["endpoints"]}
    # first 是首选，调用 1 次且成功，fallback_count=0
    assert by_prov["first"]["success"] == 1
    assert by_prov["first"]["fallback_count"] == 0
    # second 不应该被调用
    assert by_prov["second"]["total"] == 0
    assert by_prov["second"]["fallback_count"] == 0


@pytest.mark.asyncio
async def test_fallback_rate_calculation() -> None:
    """多次调用后 fallback_rate 正确。"""
    class _SometimesFail:
        def __init__(self) -> None:
            self.n = 0

        async def get_a(self) -> str:
            self.n += 1
            if self.n % 2 == 1:
                raise RuntimeError("alternating fail")
            return "ok"

    first = _SometimesFail()
    second = _FakeProvider()
    router = DataRouter(
        providers={"first": first, "second": second},
        method_table={"get_a": {"first": "get_a", "second": "get_a"}},
        default_strict=False,
        cache_ttl=0,  # 关闭 cache，确保每次都打 provider
    )
    # 调 3 次：1st fail (fallback), 2nd ok, 3rd fail (fallback)
    await router.route("get_a", use_cache=False)  # fallback
    await router.route("get_a", use_cache=False)  # first ok
    await router.route("get_a", use_cache=False)  # fallback

    by_prov = {e["provider"]: e for e in router.health()["endpoints"]}
    # first: 3 调用，1 成功 2 失败，fallback_count=0
    assert by_prov["first"]["total"] == 3
    assert by_prov["first"]["success"] == 1
    assert by_prov["first"]["failure"] == 2
    assert by_prov["first"]["fallback_count"] == 0
    # second: 2 调用都成功（都是 fallback），fallback_count=2, fallback_rate=1.0
    assert by_prov["second"]["total"] == 2
    assert by_prov["second"]["fallback_count"] == 2
    assert by_prov["second"]["fallback_rate"] == 1.0


def test_reset_stats_clears_fallback_count() -> None:
    """reset_stats 清空所有统计包括 fallback_count。"""
    s = EndpointStats(method="m", provider="p")
    s.record(success=False, latency_ms=10.0, error="x", is_fallback=True)
    assert s.fallback_count == 1
    s.total = 0; s.success = 0; s.failure = 0
    s.fallback_count = 0; s.last_error = ""; s.latencies_ms.clear()
    assert s.fallback_count == 0
    assert s.total == 0


# ── 分类查询 ──────────────────────────────────────────────────────────

def test_router_get_category_for_method() -> None:
    """method → category 分类查询（默认分类表）。"""
    # 用一个已知的 default method_table 里的方法
    from data_pipeline.providers.a_stock_data_provider import AStockDataProvider
    router = DataRouter(providers={"a_stock_data": AStockDataProvider()})
    # get_daily_bars 在默认 method_table 里的 akshare，但 a_stock_data provider 没有这个方法
    # 所以用 get_kline（a_stock_data 唯一有）
    assert router.get_category_for_method("get_kline") == "bars_and_indicators"


def test_router_unknown_method_raises() -> None:
    """未注册的 method 调用 route 抛 ValueError。"""
    router = _build_test_router()
    with pytest.raises(ValueError):
        asyncio.run(router.route("nonexistent_method"))


# ── 真实 akshare provider 集成测试（验证 provider 注册） ───────────────

def test_router_akshare_provider_registered() -> None:
    """默认 router 的 akshare + a_stock_data provider 已注册 CORE method。

    注：默认不再包含 EXTENDED（财报三表/分钟线/高级事件/个股龙虎榜），
    需要时通过 `DataRouter(include_extended=True)` 启用。
    """
    router = DataRouter()
    methods = router.available_methods()
    assert "get_stock_list" in methods
    assert "get_daily_bars" in methods
    assert "get_sector_recent_bars" in methods
    assert "get_kline" in methods  # a_stock_data
    assert "get_tencent_quote" in methods  # a_stock_data
    assert "get_dragon_tiger" in methods  # a_stock_data
    assert "get_concept_blocks" in methods  # a_stock_data
    # provider 列表应包含 akshare 和 a_stock_data
    assert "akshare" in router.providers
    assert "a_stock_data" in router.providers
    # EXTENDED 默认不注册
    assert "get_balance_sheet" not in methods
    assert "get_minute_bars" not in methods
    assert "get_stock_lhb" not in methods


def test_default_method_table_excludes_extended() -> None:
    """默认 router 不加载仅存在于 EXTENDED 的端点。"""
    router = DataRouter()
    methods = set(router.available_methods())
    for extended in DataRouter.EXTENDED_METHOD_TABLE:
        if extended in DataRouter.CORE_METHOD_TABLE:
            assert extended in methods, f"{extended} 已提升到 CORE，默认 router 应可用"
        else:
            assert extended not in methods, f"{extended} 不应在默认 method_table 里"


def test_include_extended_loads_extended_methods() -> None:
    """`include_extended=True` 时 EXTENDED 端点也注册进来。"""
    router = DataRouter(include_extended=True)
    methods = set(router.available_methods())
    # CORE 全在
    for core in DataRouter.CORE_METHOD_TABLE:
        assert core in methods
    # EXTENDED 全在
    for ext in DataRouter.EXTENDED_METHOD_TABLE:
        assert ext in methods, f"include_extended=True 应注册 {ext}"


def test_register_method_dynamic() -> None:
    """运行时 `register_method` 动态注册新 method，能正常路由。"""
    provider = _FakeProvider()
    router = DataRouter(providers={"fake": provider}, method_table={})
    # 初始没有 get_a
    assert "get_a" not in router.available_methods()
    # 动态注册
    router.register_method("get_a", "fake", category="custom")
    assert "get_a" in router.available_methods()
    # 分类已记录
    assert router.get_category_for_method("get_a") == "custom"
    # 可正常路由
    result = asyncio.run(router.route("get_a", use_cache=False))
    assert result == "a-ok"
    # 端点 stats 初始化
    assert "get_a:fake" in router._stats


def test_register_method_unknown_provider_raises() -> None:
    """注册到不存在的 provider → KeyError。"""
    router = DataRouter(providers={"fake": _FakeProvider()}, method_table={})
    with pytest.raises(KeyError):
        router.register_method("get_a", "nonexistent")


def test_register_method_unknown_attr_raises() -> None:
    """注册 provider 上不存在的属性 → AttributeError。"""
    router = DataRouter(providers={"fake": _FakeProvider()}, method_table={})
    with pytest.raises(AttributeError):
        router.register_method("get_a", "fake", attr="nonexistent_method")
