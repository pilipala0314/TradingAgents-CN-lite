"""L2 多源融合单测（无网络，纯函数）。"""
from datetime import datetime, timedelta, timezone

import pytest

from foundation.registry import (
    AvailabilitySpec,
    DisclosureProfile,
    FactorSpec,
    FusionSpec,
    FusionStrategy,
    SourceSpec,
)
from foundation.schemas import (
    Caliber,
    DataStatus,
    FactorDimension,
    SourceAgreement,
    SourceEvidence,
)

from indicators.fusion import SourceResult, fuse, upstream_key

_TZ = timezone(timedelta(hours=8))
_TRADE = datetime(2026, 9, 30, tzinfo=_TZ)
_CALIBER = {
    "eastmoney": Caliber.EASTMONEY,
    "a_stock_data": Caliber.UNKNOWN,
    "sina": Caliber.SINA,
    "akshare": Caliber.AKSHARE,
    "tushare": Caliber.TUSHARE,
}


def _src(provider="eastmoney", method="get_x", priority=0, field="v") -> SourceSpec:
    return SourceSpec(
        provider=provider, method=method,
        caliber=_CALIBER.get(provider, Caliber.UNKNOWN),
        priority=priority, field=field,
    )


def _res(spec: SourceSpec, status=DataStatus.OK, value=None, payload=None, order=0, error=None) -> SourceResult:
    evidence = SourceEvidence(
        source=spec.provider, method=spec.method, status=status,
        as_of=_TRADE if status == DataStatus.OK else None,
        fetched_at=_TRADE, error=error,
    )
    return SourceResult(spec=spec, order=order, status=status, value=value, payload=payload, evidence=evidence)


def _factor(strategy=FusionStrategy.SINGLE, threshold=0.05, require_agreement=False, sources=None) -> FactorSpec:
    return FactorSpec(
        id="f", dimension=FactorDimension.PRICE, name="F",
        availability=AvailabilitySpec(profile=DisclosureProfile.AFTER_CLOSE),
        sources=sources or [_src()],
        fusion=FusionSpec(strategy=strategy, conflict_threshold=threshold, require_agreement=require_agreement),
    )


@pytest.mark.unit
class TestUpstreamKey:
    def test_same_upstream(self):
        assert upstream_key("eastmoney") == upstream_key("a_stock_data") == "em"

    def test_other_providers_are_themselves(self):
        assert upstream_key("sina") == "sina"
        assert upstream_key("akshare") == "akshare"


@pytest.mark.unit
class TestBasicFusion:
    def test_single_ok(self):
        spec = _factor()
        f = fuse(spec, [_res(_src(), value=2.35)])
        assert f.value == pytest.approx(2.35)
        assert f.confidence == 0.6
        assert f.source_agreement == SourceAgreement.SINGLE
        assert f.caliber == Caliber.EASTMONEY
        assert f.as_of == _TRADE
        assert f.is_usable is True

    def test_all_failed_keeps_declared_caliber(self):
        spec = _factor()
        f = fuse(spec, [_res(_src(provider="sina"), status=DataStatus.FAILED, error="boom")])
        assert f.value is None
        assert f.confidence == 0.0
        assert f.source_agreement == SourceAgreement.SINGLE
        assert f.caliber == Caliber.SINA  # 无 OK 源 → 声明源口径（供审计）
        assert f.is_usable is False

    def test_first_respects_priority_not_list_order(self):
        spec = _factor(strategy=FusionStrategy.FIRST, sources=[
            _src(provider="eastmoney", priority=0),
            _src(provider="sina", priority=2),
        ])
        f = fuse(spec, [
            _res(_src(provider="sina", priority=2), value=9.0, order=1),
            _res(_src(provider="eastmoney", priority=0), value=1.0, order=0),
        ])
        assert f.value == pytest.approx(1.0)

    def test_mean_and_median(self):
        spec_mean = _factor(strategy=FusionStrategy.MEAN)
        f = fuse(spec_mean, [
            _res(_src(provider="eastmoney"), value=1.0, order=0),
            _res(_src(provider="sina"), value=3.0, order=1),
        ])
        assert f.value == pytest.approx(2.0)

        spec_median = _factor(strategy=FusionStrategy.MEDIAN)
        f = fuse(spec_median, [
            _res(_src(provider="eastmoney"), value=1.0, order=0),
            _res(_src(provider="sina"), value=3.0, order=1),
            _res(_src(provider="akshare"), value=2.0, order=2),
        ])
        assert f.value == pytest.approx(2.0)

    def test_vote(self):
        spec = _factor(strategy=FusionStrategy.VOTE)
        f = fuse(spec, [
            _res(_src(provider="eastmoney"), value="a", order=0),
            _res(_src(provider="sina"), value="a", order=1),
            _res(_src(provider="akshare"), value="b", order=2),
        ])
        assert f.value == "a"

    def test_union_dedup(self):
        spec = _factor(strategy=FusionStrategy.UNION_DEDUP)
        item = {"title": "x", "url": "u1"}
        f = fuse(spec, [
            _res(_src(provider="news"), payload=[item, {"title": "y"}], order=0),
            _res(_src(provider="sina"), payload=[dict(item)], order=1),
        ])
        assert f.value == 2  # 去重后 2 条
        assert len(f.raw_by_source["__fused__"]) == 2


@pytest.mark.unit
class TestAgreement:
    def test_agree_independent_two_sources(self):
        spec = _factor(strategy=FusionStrategy.MEDIAN, threshold=0.05, sources=[
            _src(provider="eastmoney"), _src(provider="sina", priority=1),
        ])
        f = fuse(spec, [
            _res(_src(provider="eastmoney"), value=2.30, order=0),
            _res(_src(provider="sina"), value=2.31, order=1),
        ])
        assert f.source_agreement == SourceAgreement.AGREE
        assert f.confidence == 0.80  # n_indep=2
        assert f.conflicts == []

    def test_pseudo_multisource_no_boost(self):
        """eastmoney + a_stock_data 同上游 → 独立源数=1，置信度不给独立双源加成。"""
        spec = _factor(strategy=FusionStrategy.MEDIAN, threshold=0.05, sources=[
            _src(provider="eastmoney"), _src(provider="a_stock_data", priority=1),
        ])
        f = fuse(spec, [
            _res(_src(provider="eastmoney"), value=2.30, order=0),
            _res(_src(provider="a_stock_data"), value=2.31, order=1),
        ])
        assert f.source_agreement == SourceAgreement.AGREE
        assert f.confidence == 0.70

    def test_conflict_with_details(self):
        spec = _factor(strategy=FusionStrategy.MEDIAN, threshold=0.05, sources=[
            _src(provider="eastmoney"), _src(provider="sina", priority=1),
        ])
        f = fuse(spec, [
            _res(_src(provider="eastmoney"), value=1.0, order=0),
            _res(_src(provider="sina"), value=1.5, order=1),
        ])
        assert f.source_agreement == SourceAgreement.CONFLICT
        assert f.confidence == 0.3
        assert f.is_usable is False
        assert len(f.conflicts) == 1
        c = f.conflicts[0]
        assert c.field == "v"
        assert c.values == {"eastmoney": 1.0, "sina": 1.5}
        assert c.deviation == pytest.approx(0.4)
        assert c.threshold == 0.05

    def test_conflict_near_zero_mean(self):
        spec = _factor(strategy=FusionStrategy.MEAN, threshold=0.05, sources=[
            _src(provider="eastmoney"), _src(provider="sina", priority=1),
        ])
        f = fuse(spec, [
            _res(_src(provider="eastmoney"), value=1e-10, order=0),
            _res(_src(provider="sina"), value=-1e-10, order=1),
        ])
        assert f.source_agreement == SourceAgreement.CONFLICT
        assert f.conflicts[0].deviation == pytest.approx(1e9)

    def test_require_agreement_single_lowered(self):
        spec = _factor(require_agreement=True)
        f = fuse(spec, [_res(_src(), value=1.0)])
        assert f.confidence == 0.4

    def test_one_failed_one_ok_stays_single(self):
        spec = _factor(strategy=FusionStrategy.FIRST, sources=[
            _src(provider="eastmoney"), _src(provider="sina", priority=1),
        ])
        f = fuse(spec, [
            _res(_src(provider="eastmoney"), status=DataStatus.FAILED, order=0, error="blocked"),
            _res(_src(provider="sina"), value=1.0, order=1),
        ])
        assert f.value == pytest.approx(1.0)
        assert f.source_agreement == SourceAgreement.SINGLE
        assert f.confidence == 0.6
        assert [e.status for e in f.evidence] == [DataStatus.FAILED, DataStatus.OK]
        assert f.raw_by_source == {"sina": 1.0}

    def test_non_numeric_equal_and_conflict(self):
        spec = _factor(strategy=FusionStrategy.FIRST, sources=[
            _src(provider="eastmoney"), _src(provider="sina", priority=1),
        ])
        f_ok = fuse(spec, [
            _res(_src(provider="eastmoney"), value="半导体", order=0),
            _res(_src(provider="sina"), value="半导体", order=1),
        ])
        assert f_ok.source_agreement == SourceAgreement.AGREE

        f_bad = fuse(spec, [
            _res(_src(provider="eastmoney"), value="A", order=0),
            _res(_src(provider="sina"), value="B", order=1),
        ])
        assert f_bad.source_agreement == SourceAgreement.CONFLICT
        assert f_bad.conflicts[0].deviation == pytest.approx(1e9)