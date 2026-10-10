"""L2 编排单测（无网络：FakeProvider 回放 + 注入 ds/registry）。"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from datasource.router import DataSource
from datasource.resilience import ResiliencePolicy
from datasource.resilience.base import (
    BulkheadConfig,
    CircuitBreakerConfig,
    RateLimitConfig,
    RetryConfig,
)
from foundation.registry import (
    AvailabilitySpec,
    DisclosureProfile,
    ExtractKind,
    FactorRegistry,
    FactorSpec,
    FusionSpec,
    FusionStrategy,
    SourceSpec,
    load_registry,
)
from foundation.schemas import Caliber, DataStatus, FactorDimension, IndicatorPack

from indicators.pipeline import DEFAULT_REGISTRY_PATH, build_sector_pack
from indicators.store import pack_path, write_pack

_TZ = timezone(timedelta(hours=8))


class FakeProvider:
    """按表回放的假 provider：method → payload | Exception | callable。"""

    def __init__(self, table: dict, name: str = "fake"):
        self.table = table
        self.calls: list[tuple] = []
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def __getattr__(self, item):
        table = self.__dict__.get("table", {})
        if item not in table:
            raise AttributeError(item)

        async def _call(*args, **kwargs):
            self.calls.append((item, args, kwargs))
            value = table[item]
            if isinstance(value, Exception):
                raise value
            if callable(value):
                return value(*args, **kwargs)
            return value

        return _call


def _policy() -> ResiliencePolicy:
    """宽松策略：单测只关心编排语义，不做重试/限流等待。"""
    return ResiliencePolicy(
        retry=RetryConfig(max_attempts=1),
        rate_limit=RateLimitConfig(rps=100.0, burst=100),
        circuit=CircuitBreakerConfig(failure_threshold=1000),
        bulkhead=BulkheadConfig(max_concurrent=50),
    )


def _ds(table: dict, provider: str = "fake") -> DataSource:
    methods = {m: {provider: m} for m in table}
    return DataSource(
        providers={provider: FakeProvider(table, name=provider)},
        method_table=methods,
        cache_ttl=0,
        default_resilience=_policy(),
    )


def _src(method: str = "get_ok", provider: str = "fake", **kw) -> SourceSpec:
    base = dict(provider=provider, method=method, caliber=Caliber.AKSHARE)
    base.update(kw)
    return SourceSpec(**base)


def _factor(
    fid: str,
    dimension: FactorDimension = FactorDimension.PRICE,
    sources: list[SourceSpec] | None = None,
    strategy: FusionStrategy = FusionStrategy.SINGLE,
    requires_components: bool = False,
    availability: AvailabilitySpec | None = None,
) -> FactorSpec:
    return FactorSpec(
        id=fid,
        dimension=dimension,
        name=fid,
        availability=availability or AvailabilitySpec(profile=DisclosureProfile.AFTER_CLOSE),
        sources=sources or [_src()],
        fusion=FusionSpec(strategy=strategy),
        requires_components=requires_components,
    )


def _registry(*factors: FactorSpec) -> FactorRegistry:
    return FactorRegistry(name="test", factors=list(factors))


@pytest.mark.unit
class TestPipelineBasics:
    def test_ok_and_failed_dimensions(self):
        reg = _registry(
            _factor("f_ok", sources=[_src(method="get_ok", field="v")]),
            _factor("f_fail", dimension=FactorDimension.CAPITAL, sources=[_src(method="get_fail", field="v")]),
        )
        ds = _ds({"get_ok": [{"v": 2.35}], "get_fail": ConnectionError("blocked")})
        pack = asyncio.run(build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds))

        assert isinstance(pack, IndicatorPack)
        factors = {f.id: f for f in pack.factors}
        assert len(factors) == 2
        assert factors["f_ok"].value == pytest.approx(2.35)
        assert factors["f_ok"].evidence[0].status == DataStatus.OK
        assert factors["f_fail"].value is None
        assert factors["f_fail"].evidence[0].status == DataStatus.FAILED

        quality = {e.dimension: e for e in pack.data_quality}
        assert quality["price"].status == DataStatus.OK
        assert quality["capital"].status == DataStatus.FAILED

        summary = pack.summary()
        assert summary["factors_total"] == 2
        assert summary["factors_usable"] == 1
        assert pack.meta.sector == "半导体"
        assert pack.meta.trade_date == "2026-09-30"
        assert pack.meta.produced_by == "indicators"

    def test_unresolved_template_fails_without_call(self):
        reg = _registry(_factor("f_var", sources=[_src(method="get_ok", args={"foo": "{unknown_var}"})]))
        ds = _ds({"get_ok": [{"v": 1}]})
        pack = asyncio.run(build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds))
        f = pack.factors[0]
        assert f.evidence[0].status == DataStatus.FAILED
        assert "未解析模板变量" in (f.evidence[0].error or "")
        assert ds.providers["fake"].calls == []

    def test_all_error_results_have_evidence(self):
        reg = _registry(_factor("f1", sources=[_src(method="get_a"), _src(method="get_b", priority=1)]))
        ds = _ds({"get_a": ConnectionError("x"), "get_b": ValueError("y")})
        pack = asyncio.run(build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds))
        f = pack.factors[0]
        assert f.value is None
        assert len(f.evidence) == 2
        assert all(e.status == DataStatus.FAILED for e in f.evidence)


@pytest.mark.unit
class TestTimeGate:
    def test_gate_only_for_today_and_force_round_bypass(self):
        avail = AvailabilitySpec(profile=DisclosureProfile.AFTER_CLOSE, available_from="15:30")
        reg = _registry(_factor("f_gate", sources=[_src(method="get_ok", field="v")], availability=avail))
        ds = _ds({"get_ok": [{"v": 1.0}]})
        now = datetime(2026, 10, 9, 15, 0, tzinfo=_TZ)

        # 当日 15:00 < 15:30 → 闸门（且不拉取）
        pack = asyncio.run(build_sector_pack("半导体", "2026-10-09", registry=reg, ds=ds, now=now))
        assert pack.factors[0].value is None
        assert pack.factors[0].evidence[0].status == DataStatus.UNAVAILABLE_YET
        assert ds.providers["fake"].calls == []

        # force 放行
        pack2 = asyncio.run(build_sector_pack("半导体", "2026-10-09", registry=reg, ds=ds, now=now, force=True))
        assert pack2.factors[0].value == pytest.approx(1.0)

        # round>=2 跳过 available_from
        pack3 = asyncio.run(
            build_sector_pack("半导体", "2026-10-09", registry=reg, ds=ds, now=now, generation_round=2)
        )
        assert pack3.factors[0].value == pytest.approx(1.0)

        # 历史日不闸（数据已披露）
        pack4 = asyncio.run(build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds, now=now))
        assert pack4.factors[0].value == pytest.approx(1.0)

    def test_next_day_profile_gated_even_round2(self):
        avail = AvailabilitySpec(profile=DisclosureProfile.NEXT_DAY)
        reg = _registry(_factor("f_nd", sources=[_src(method="get_ok")], availability=avail))
        ds = _ds({"get_ok": [{"v": 1.0}]})
        now = datetime(2026, 10, 9, 22, 0, tzinfo=_TZ)
        pack = asyncio.run(
            build_sector_pack("半导体", "2026-10-09", registry=reg, ds=ds, now=now, generation_round=2)
        )
        assert pack.factors[0].evidence[0].status == DataStatus.UNAVAILABLE_YET


@pytest.mark.unit
class TestComponents:
    def test_count_with_component_set(self):
        reg = _registry(
            _factor(
                "f_limit",
                dimension=FactorDimension.SENTIMENT,
                sources=[_src(method="get_pool", match={"代码": "{component}"}, extract_kind=ExtractKind.COUNT)],
                requires_components=True,
            )
        )
        ds = _ds(
            {
                "get_industry_board_components": [{"代码": "600519"}, {"代码": "000001"}, {"代码": "300750"}],
                "get_pool": [{"代码": "600519"}, {"代码": "999999"}, {"代码": "000001"}],
            }
        )
        pack = asyncio.run(build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds))
        f = pack.factors[0]
        assert f.value == 2
        assert pack.meta.symbols == ["600519", "000001", "300750"]

    def test_components_failed_degrades_without_pool_call(self):
        reg = _registry(
            _factor(
                "f_limit",
                sources=[_src(method="get_pool", match={"代码": "{component}"}, extract_kind=ExtractKind.COUNT)],
                requires_components=True,
            )
        )
        ds = _ds(
            {
                "get_industry_board_components": ConnectionError("blocked"),
                "get_concept_board_components": ConnectionError("blocked"),
                "get_pool": [{"代码": "600519"}],
            }
        )
        pack = asyncio.run(build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds))
        f = pack.factors[0]
        assert f.value is None
        assert f.evidence[0].status == DataStatus.FAILED
        assert "成分股不可用" in (f.evidence[0].error or "")
        assert all(call[0] != "get_pool" for call in ds.providers["fake"].calls)

    def test_component_loop_sum_with_date_match(self):
        amounts = {"600519": 1e8, "000001": 2e8}

        def _flow(*args, **kwargs):
            code = kwargs.get("symbol")
            # 两行：当日行 + 历史行（match 只应命中当日，验证日期格式归一）
            return [{"日期": "2026-09-30", "v": amounts.get(code, 0)}, {"日期": "2026-09-29", "v": 9e9}]

        reg = _registry(
            _factor(
                "f_member",
                dimension=FactorDimension.CAPITAL,
                sources=[
                    _src(
                        method="get_flow",
                        args={"symbol": "{component}"},
                        match={"日期": "{trade_date}"},
                        extract_kind=ExtractKind.SUM,
                        field="v",
                        unit_scale=1e-8,
                    )
                ],
                requires_components=True,
            )
        )
        ds = _ds(
            {
                "get_industry_board_components": [{"代码": "600519"}, {"代码": "000001"}],
                "get_flow": _flow,
            }
        )
        pack = asyncio.run(build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds))
        f = pack.factors[0]
        assert f.value == pytest.approx(3.0)  # (1e8 + 2e8) * 1e-8，且只取当日行
        assert "ok=2" in str(f.evidence[0].preview)


@pytest.mark.unit
class TestStore:
    def test_write_readback_and_overwrite(self, tmp_path):
        reg = _registry(_factor("f_ok", sources=[_src(method="get_ok", field="v")]))
        ds = _ds({"get_ok": [{"v": 1.5}]})
        pack = asyncio.run(
            build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds, generation_round=2)
        )
        path = pack_path(tmp_path, "2026-09-30", "半导体", 2)
        assert path.name == "sector_半导体_g2.json"

        write_pack(pack, path)
        assert path.exists()
        assert not path.with_suffix(".json.tmp").exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        reloaded = IndicatorPack.model_validate(data)
        assert reloaded.factors[0].value == pytest.approx(1.5)
        assert reloaded.meta.generation_round == 2

        write_pack(pack, path)  # 覆盖重跑
        assert json.loads(path.read_text(encoding="utf-8"))["meta"]["generation_round"] == 2


@pytest.mark.unit
class TestRealRegistryUnderTotalBlock:
    def test_real_registry_produces_honest_pack(self):
        """真实注册表 + 全阻断假源：15 因子齐全、全部无值、每条有溯源（封锁下的诚实降级）。"""
        reg = load_registry(DEFAULT_REGISTRY_PATH)
        methods_by_prov: dict[str, set[str]] = {}
        for f in reg.factors:
            for s in f.sources:
                methods_by_prov.setdefault(s.provider, set()).add(s.method)

        providers = {
            p: FakeProvider({m: ConnectionError("simulated block") for m in ms}, name=p)
            for p, ms in methods_by_prov.items()
        }
        all_methods = {m for ms in methods_by_prov.values() for m in ms}
        table = {m: {p: m for p, ms in methods_by_prov.items() if m in ms} for m in all_methods}
        ds = DataSource(
            providers=providers, method_table=table, cache_ttl=0, default_resilience=_policy(),
        )

        pack = asyncio.run(build_sector_pack("半导体", "2026-09-30", registry=reg, ds=ds))
        assert len(pack.factors) == 15
        assert all(f.value is None for f in pack.factors)
        assert all(f.evidence for f in pack.factors)
        assert all(not f.is_usable for f in pack.factors)
        assert pack.data_quality
        # 无任何"假值"：所有因子 value 必须为 None（不能是 0/编造）
        assert all(f.confidence == 0.0 for f in pack.factors)