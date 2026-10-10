"""DataSource 路由引擎行为测试（mock provider，无网络）。"""
import asyncio

import pytest

from datasource.errors import DataFetchError, ProviderError
from datasource.resilience.policy import ResiliencePolicy
from datasource.router import DataSource
from datasource.types import MethodTable


class _OkProvider:
    def __init__(self, value="ok"):
        self._value = value
        self.calls = 0

    @property
    def name(self):
        return "ok"

    async def get_daily_bars(self, *args, **kwargs):
        self.calls += 1
        return self._value


class _FailProvider:
    @property
    def name(self):
        return "fail"

    async def get_daily_bars(self, *args, **kwargs):
        raise ProviderError("boom", provider="fail", method="get_daily_bars")


def _build_source(providers: dict, method_table: MethodTable, **kw) -> DataSource:
    # default_resilience=空策略：禁用重试/限流/熔断，保证测试确定性与速度
    kw.setdefault("cache_ttl", 0)
    return DataSource(
        providers,
        method_table,
        default_timeout=5,
        default_resilience=ResiliencePolicy(),
        **kw,
    )


@pytest.mark.unit
class TestRouterFallback:
    def test_first_provider_success(self):
        ok = _OkProvider()
        table = {"get_daily_bars": {"ok": "get_daily_bars", "fail": "get_daily_bars"}}
        source = _build_source({"ok": ok, "fail": _FailProvider()}, table)
        assert asyncio.run(source.route("get_daily_bars", "600519")) == "ok"
        assert ok.calls == 1

    def test_fallback_on_failure(self):
        table = {"get_daily_bars": {"fail": "get_daily_bars", "ok": "get_daily_bars"}}
        source = _build_source({"fail": _FailProvider(), "ok": _OkProvider()}, table)
        assert asyncio.run(source.route("get_daily_bars", "600519")) == "ok"

    def test_all_fail_raises_data_fetch_error(self):
        table = {"get_daily_bars": {"fail": "get_daily_bars"}}
        source = _build_source({"fail": _FailProvider()}, table)
        with pytest.raises(DataFetchError):
            asyncio.run(source.route("get_daily_bars", "600519"))

    def test_unknown_method_raises(self):
        source = _build_source(
            {"ok": _OkProvider()}, {"get_daily_bars": {"ok": "get_daily_bars"}}
        )
        with pytest.raises(ValueError):
            asyncio.run(source.route("nope", "600519"))

    def test_forced_provider_only(self):
        """强制指定 provider：不触发 fallback。"""
        ok = _OkProvider(value="forced")
        table = {"get_daily_bars": {"ok": "get_daily_bars", "fail": "get_daily_bars"}}
        source = _build_source({"ok": ok, "fail": _FailProvider()}, table)
        result = asyncio.run(source.route("get_daily_bars", "600519", provider="ok"))
        assert result == "forced"
        assert ok.calls == 1


@pytest.mark.unit
class TestRouterCache:
    def test_cache_hit_avoids_provider(self):
        ok = _OkProvider(value="cached")
        table = {"get_daily_bars": {"ok": "get_daily_bars"}}
        source = _build_source({"ok": ok}, table, cache_ttl=30)
        r1 = asyncio.run(source.route("get_daily_bars", "600519"))
        r2 = asyncio.run(source.route("get_daily_bars", "600519"))
        assert r1 == r2 == "cached"
        assert ok.calls == 1  # 第二次命中缓存

    def test_cache_off_when_ttl_zero(self):
        ok = _OkProvider(value="nocache")
        table = {"get_daily_bars": {"ok": "get_daily_bars"}}
        source = _build_source({"ok": ok}, table, cache_ttl=0)
        asyncio.run(source.route("get_daily_bars", "600519"))
        asyncio.run(source.route("get_daily_bars", "600519"))
        assert ok.calls == 2


@pytest.mark.unit
class TestRouterQuality:
    def test_none_rejected_as_failure(self):
        class _NoneProvider:
            @property
            def name(self):
                return "none"

            async def get_daily_bars(self, *args, **kwargs):
                return None

        table = {"get_daily_bars": {"none": "get_daily_bars"}}
        source = _build_source({"none": _NoneProvider()}, table)
        with pytest.raises(DataFetchError):
            asyncio.run(source.route("get_daily_bars", "600519"))

    def test_empty_list_is_valid(self):
        class _EmptyProvider:
            @property
            def name(self):
                return "empty"

            async def get_daily_bars(self, *args, **kwargs):
                return []

        table = {"get_daily_bars": {"empty": "get_daily_bars"}}
        source = _build_source({"empty": _EmptyProvider()}, table)
        assert asyncio.run(source.route("get_daily_bars", "600519")) == []

    def test_empty_selector_result_falls_through(self):
        """选择器类方法：空结果=失败 → 继续 fallback（防"空即成功"短路多源链）。"""

        class _EmptySel:
            @property
            def name(self):
                return "empty_sel"

            async def get_industry_ranking(self, *args, **kwargs):
                return []

        class _OkSel:
            @property
            def name(self):
                return "ok_sel"

            async def get_industry_ranking(self, *args, **kwargs):
                return [{"board_name": "半导体"}]

        table = {
            "get_industry_ranking": {
                "empty_sel": "get_industry_ranking",
                "ok_sel": "get_industry_ranking",
            }
        }
        source = _build_source({"empty_sel": _EmptySel(), "ok_sel": _OkSel()}, table)
        assert asyncio.run(source.route("get_industry_ranking")) == [{"board_name": "半导体"}]

    def test_all_empty_selector_raises(self):
        """选择器类方法：全部源都空 → DataFetchError（不静默返空）。"""

        class _EmptySel2:
            @property
            def name(self):
                return "empty_sel2"

            async def get_industry_ranking(self, *args, **kwargs):
                return []

        table = {"get_industry_ranking": {"empty_sel2": "get_industry_ranking"}}
        source = _build_source({"empty_sel2": _EmptySel2()}, table)
        with pytest.raises(DataFetchError):
            asyncio.run(source.route("get_industry_ranking"))


@pytest.mark.unit
class TestRouterHealth:
    def test_health_report(self):
        table = {"get_daily_bars": {"ok": "get_daily_bars"}}
        source = _build_source({"ok": _OkProvider()}, table)
        asyncio.run(source.route("get_daily_bars", "600519"))
        health = source.health()
        assert health["calls_total"] == 1
        assert health["calls_success"] == 1
