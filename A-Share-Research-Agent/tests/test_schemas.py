"""foundation.schemas 契约单测 — 锁定层间共享 schema。

重点验证三条设计约束：
1. 三态 data_quality 语义区分（empty / unavailable_yet / failed）
2. 口径不合并（caliber 独立标注）
3. 数字可溯源（cited_values 绑定 factor_id）
"""
from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from foundation.schemas import (
    SCHEMA_VERSION,
    Caliber,
    ConclusionPack,
    CitedValue,
    DataQualityEntry,
    DataStatus,
    DebateVerdict,
    Factor,
    FieldConflict,
    FactorDimension,
    FactorUse,
    IndicatorPack,
    PackMeta,
    PackScope,
    SourceAgreement,
    SourceEvidence,
    Stance,
)


def _evidence(source: str = "eastmoney", method: str = "get_sector_fund_flow", **kw) -> SourceEvidence:
    return SourceEvidence(source=source, method=method, status=kw.pop("status", DataStatus.OK), **kw)


def _factor(**kw) -> Factor:
    base = dict(
        id="sector_capital_net_inflow",
        dimension=FactorDimension.CAPITAL,
        name="板块主力净流入",
        value=12.5,
        unit="亿元",
        caliber=Caliber.EASTMONEY,
    )
    base.update(kw)
    return Factor(**base)


def _pack(**kw) -> IndicatorPack:
    meta = PackMeta(trade_date="2026-10-08", scope=PackScope.SECTOR, sector="半导体")
    return IndicatorPack(meta=meta, **kw)


# ── 溯源层 ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestSourceEvidence:
    def test_minimal(self):
        e = _evidence()
        assert e.source == "eastmoney"
        assert e.status is DataStatus.OK
        assert e.as_of is None and e.fetched_at is None

    def test_status_all_values(self):
        # 三态必须是不同的枚举值，禁止混用
        values = {s.value for s in DataStatus}
        assert {"ok", "empty", "unavailable_yet", "failed", "conflict"} == values

    def test_negative_latency_rejected(self):
        with pytest.raises(ValidationError):
            SourceEvidence(source="x", method="m", status=DataStatus.OK, latency_ms=-1)


@pytest.mark.unit
class TestCaliber:
    def test_caliber_is_metadata_not_merged(self):
        """口径作为独立元数据存在，不同口径值不得被归一。"""
        assert Caliber.EASTMONEY.value != Caliber.SW.value
        assert Caliber.UNKNOWN.value == "unknown"


@pytest.mark.unit
class TestFieldConflict:
    def test_conflict_detail(self):
        c = FieldConflict(
            field="pct_change",
            values={"eastmoney": 2.31, "akshare": 2.28},
            deviation=0.013,
            threshold=0.01,
        )
        assert len(c.values) == 2
        assert c.deviation >= c.threshold


# ── 因子层 ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestFactor:
    def test_default_is_single_source(self):
        f = _factor()
        assert f.source_agreement is SourceAgreement.SINGLE
        assert f.confidence == 1.0
        assert f.is_usable is True

    def test_conflict_factor_not_usable(self):
        f = _factor(
            source_agreement=SourceAgreement.CONFLICT,
            confidence=0.3,
            conflicts=[
                FieldConflict(field="value", values={"a": 1.0, "b": 2.0}, deviation=0.7)
            ],
        )
        assert f.is_usable is False

    def test_none_value_not_usable(self):
        f = _factor(value=None)
        assert f.is_usable is False

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            _factor(confidence=1.5)
        with pytest.raises(ValidationError):
            _factor(confidence=-0.1)

    def test_audit_trail_shape(self):
        f = _factor(evidence=[_evidence(source="eastmoney"), _evidence(source="akshare")])
        trail = f.audit_trail()
        assert trail["sources"] == ["eastmoney", "akshare"]
        assert trail["caliber"] == "eastmoney"
        assert trail["source_agreement"] == "single"


@pytest.mark.unit
class TestMultiSourceFusion:
    """多源融合的三种结果都要能表达。"""

    def test_agree_multi_source(self):
        f = _factor(
            source_agreement=SourceAgreement.AGREE,
            confidence=0.9,
            evidence=[_evidence("eastmoney"), _evidence("akshare", method="get_sector_fund_flow")],
        )
        assert f.is_usable is True
        assert len(f.evidence) == 2

    def test_single_source_marks_isolation(self):
        f = _factor(source_agreement=SourceAgreement.SINGLE, confidence=0.6)
        assert f.is_usable is True  # 孤证可用，但置信度低
        assert f.confidence < 0.9

    def test_conflict_keeps_both_calibers(self):
        """口径分歧不合并 — 两个源的值都保留在 conflicts 里。"""
        f = _factor(
            source_agreement=SourceAgreement.CONFLICT,
            value=None,
            conflicts=[
                FieldConflict(
                    field="net_inflow",
                    values={"eastmoney": 12.5, "akshare": 9.8},
                    deviation=0.24,
                    threshold=0.05,
                )
            ],
        )
        assert f.conflicts[0].values["eastmoney"] == 12.5
        assert f.conflicts[0].values["akshare"] == 9.8


# ── 指标包 ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestPackMeta:
    def test_schema_version(self):
        assert PackMeta(trade_date="2026-10-08", scope=PackScope.SECTOR).schema_version == SCHEMA_VERSION

    def test_sector_caliber_default_unknown(self):
        m = PackMeta(trade_date="2026-10-08", scope=PackScope.SECTOR)
        assert m.sector_caliber is Caliber.UNKNOWN

    def test_generation_round(self):
        m = PackMeta(trade_date="2026-10-08", scope=PackScope.SECTOR, generation_round=2)
        assert m.generation_round == 2
        with pytest.raises(ValidationError):
            PackMeta(trade_date="2026-10-08", scope=PackScope.SECTOR, generation_round=0)


@pytest.mark.unit
class TestDataQualityThreeStates:
    """三态语义必须可区分 — 这是"禁止脑补缺失数据"的机制基础。"""

    def test_empty_is_not_unavailable(self):
        empty = DataQualityEntry(dimension="dragon_tiger", status=DataStatus.EMPTY)
        not_yet = DataQualityEntry(dimension="dragon_tiger", status=DataStatus.UNAVAILABLE_YET)
        assert empty.status != not_yet.status

    def test_blocks_conclusion_semantics(self):
        # empty 不阻塞（确实无数据，可下"无事件"结论）
        assert DataQualityEntry(dimension="d", status=DataStatus.EMPTY).blocks_conclusion is False
        assert DataQualityEntry(dimension="d", status=DataStatus.OK).blocks_conclusion is False
        # 其余三态阻塞（只能声明不可用）
        for st in (DataStatus.UNAVAILABLE_YET, DataStatus.FAILED, DataStatus.CONFLICT):
            assert DataQualityEntry(dimension="d", status=st).blocks_conclusion is True

    def test_pack_blocked_dimensions(self):
        p = _pack(data_quality=[
            DataQualityEntry(dimension="dragon_tiger", status=DataStatus.UNAVAILABLE_YET,
                             note="预计 19:00 披露"),
            DataQualityEntry(dimension="capital", status=DataStatus.OK),
        ])
        assert p.blocked_dimensions() == ["dragon_tiger"]


@pytest.mark.unit
class TestIndicatorPack:
    def test_usable_factors_filter(self):
        p = _pack(factors=[
            _factor(id="a", value=1.0, source_agreement=SourceAgreement.AGREE),
            _factor(id="b", value=2.0, source_agreement=SourceAgreement.CONFLICT),
            _factor(id="c", value=None, source_agreement=SourceAgreement.SINGLE),
        ])
        assert [f.id for f in p.usable_factors()] == ["a"]

    def test_factors_by_dimension(self):
        p = _pack(factors=[
            _factor(id="a", dimension=FactorDimension.CAPITAL),
            _factor(id="b", dimension=FactorDimension.CAPITAL),
            _factor(id="c", dimension=FactorDimension.SENTIMENT),
        ])
        grouped = p.factors_by_dimension()
        assert len(grouped["capital"]) == 2
        assert len(grouped["sentiment"]) == 1

    def test_summary_shape(self):
        p = _pack(factors=[_factor()])
        s = p.summary()
        assert s["trade_date"] == "2026-10-08"
        assert s["scope"] == "sector"
        assert s["factors_total"] == 1
        assert s["generation_round"] == 1

    def test_serialization_roundtrip(self):
        p = _pack(factors=[_factor(evidence=[_evidence()])])
        raw = p.model_dump_json()
        restored = IndicatorPack.model_validate_json(raw)
        assert restored.meta.trade_date == p.meta.trade_date
        assert restored.factors[0].id == p.factors[0].id


# ── 结论包 ──────────────────────────────────────────────────────────


@pytest.mark.unit
class TestConclusionPack:
    def test_requires_confidence_and_conclusion(self):
        with pytest.raises(ValidationError):
            ConclusionPack(
                produced_by="sector_interpreter", trade_date="2026-10-08",
                scope="sector", stance=Stance.BULLISH, conclusion="",
            )

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            ConclusionPack(
                produced_by="x", trade_date="2026-10-08", scope="sector",
                stance=Stance.NEUTRAL, conclusion="c", confidence=1.2,
            )

    def test_cited_factor_ids_collects_all(self):
        c = ConclusionPack(
            produced_by="x", trade_date="2026-10-08", scope="sector",
            stance=Stance.BULLISH, conclusion="看多", confidence=0.7,
            cited_values=[CitedValue(factor_id="f1", value=12.5, unit="亿元")],
            factors_used=[
                FactorUse(factor_id="f2", cited_values=[CitedValue(factor_id="f3", value=1.0)])
            ],
        )
        assert c.cited_factor_ids() == {"f1", "f2", "f3"}

    def test_hallucination_flag_default_false(self):
        c = ConclusionPack(
            produced_by="x", trade_date="2026-10-08", scope="sector",
            stance=Stance.BULLISH, conclusion="c", confidence=0.5,
        )
        assert c.hallucination_risk is False


@pytest.mark.unit
class TestDebateVerdict:
    def test_neutral_requires_stance(self):
        v = DebateVerdict(
            trade_date="2026-10-08", scope="sector", subject="半导体",
            stance=Stance.NEUTRAL, confidence=0.5,
            conclusion="信号相互抵消",
            neutral_justification="双方论据均未被推翻",
        )
        assert v.stance is Stance.NEUTRAL
        assert v.neutral_justification != ""

    def test_rounds_bounds(self):
        with pytest.raises(ValidationError):
            DebateVerdict(
                trade_date="2026-10-08", scope="sector",
                stance=Stance.BULLISH, confidence=0.6, conclusion="c", rounds=0,
            )
        with pytest.raises(ValidationError):
            DebateVerdict(
                trade_date="2026-10-08", scope="sector",
                stance=Stance.BULLISH, confidence=0.6, conclusion="c", rounds=5,
            )

    def test_unterminated_marks_degradation(self):
        """单方陈述降级必须显式标记，报告须注明"未经对抗"。"""
        v = DebateVerdict(
            trade_date="2026-10-08", scope="sector",
            stance=Stance.BULLISH, confidence=0.6, conclusion="c",
            rounds=1, unterminated=True,
        )
        assert v.unterminated is True

    def test_default_is_terminated_with_3_rounds(self):
        v = DebateVerdict(
            trade_date="2026-10-08", scope="sector",
            stance=Stance.BEARISH, confidence=0.55, conclusion="c",
        )
        assert v.unterminated is False
        assert v.rounds == 3
