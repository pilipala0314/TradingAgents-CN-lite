"""test_signal_persister — 信号落库纯函数 + upsert 单测。

覆盖：
- _derive_entity_id — uuid5 稳定性 + 不同 key 不同
- _coerce_confidence — 各种输入归一化
- _verdict_to_direction — verdict → direction 映射
- _extract_target_meta / _extract_blocked_meta — 兼容 dict / str
- 4 类 row 构造：market / sector / approved / blocked
- fallback 标记透传
- upsert 调 PG insert + on_conflict_do_update
"""
from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from schemas.agent_io import AgentOutputEnvelope
from services.signal_persister import (
    STRATEGY_APPROVED,
    STRATEGY_BLOCKED,
    STRATEGY_MARKET,
    STRATEGY_SECTOR,
    _build_market_row,
    _build_sector_row,
    _build_stock_row,
    _coerce_confidence,
    _coerce_date,
    _derive_entity_id,
    _extract_blocked_meta,
    _extract_target_meta,
    _row_to_dict,
    _verdict_to_direction,
    fetch_signals_latest,
    persist_committee_signals,
)


# ── 基础纯函数 ────────────────────────────────────────────────────
class TestDeriveEntityId:
    def test_same_key_same_uuid(self):
        e1 = _derive_entity_id("stock", "000636")
        e2 = _derive_entity_id("stock", "000636")
        assert e1 == e2

    def test_different_key_different_uuid(self):
        e1 = _derive_entity_id("stock", "000636")
        e2 = _derive_entity_id("stock", "000001")
        assert e1 != e2

    def test_different_entity_type_different_uuid(self):
        e1 = _derive_entity_id("stock", "X")
        e2 = _derive_entity_id("sector", "X")
        assert e1 != e2

    def test_returns_uuid_type(self):
        e = _derive_entity_id("market", "aggregate")
        assert isinstance(e, uuid.UUID)


class TestCoerceConfidence:
    def test_none_returns_0_5(self):
        assert _coerce_confidence(None) == 0.5

    def test_float_in_range(self):
        assert _coerce_confidence(0.7) == 0.7

    def test_clamp_above_1(self):
        assert _coerce_confidence(1.5) == 1.0

    def test_clamp_below_0(self):
        assert _coerce_confidence(-0.3) == 0.0

    def test_string_invalid_returns_0_5(self):
        assert _coerce_confidence("invalid") == 0.5

    def test_string_numeric(self):
        assert _coerce_confidence("0.8") == 0.8


class TestCoerceDate:
    def test_date_input(self):
        from datetime import date
        d = date(2026, 7, 3)
        assert _coerce_date(d) == d

    def test_datetime_input(self):
        from datetime import datetime
        dt = datetime(2026, 7, 3, 15, 30)
        assert _coerce_date(dt) == date(2026, 7, 3)

    def test_string_iso_input(self):
        assert _coerce_date("2026-07-03") == date(2026, 7, 3)


class TestVerdictToDirection:
    @pytest.mark.parametrize(
        "v,exp",
        [("long", "long"), ("short", "short"),
         ("neutral", "neutral"), ("avoid", "avoid")],
    )
    def test_known_verdicts(self, v, exp):
        assert _verdict_to_direction(v) == exp

    def test_unknown_verdict_defaults_neutral(self):
        assert _verdict_to_direction("weird") == "neutral"


class TestExtractTargetMeta:
    def test_str_input(self):
        sym, conf = _extract_target_meta("000636")
        assert sym == "000636"
        assert 0.5 < conf <= 0.7

    def test_dict_input(self):
        sym, conf = _extract_target_meta(
            {"symbol": "000636", "confidence": 0.85, "trend": "bullish"}
        )
        assert sym == "000636"
        assert conf == 0.85

    def test_dict_with_code_key(self):
        sym, _ = _extract_target_meta({"code": "600519"})
        assert sym == "600519"

    def test_invalid_input(self):
        sym, conf = _extract_target_meta(None)
        assert sym == ""
        assert 0.0 <= conf <= 1.0


class TestExtractBlockedMeta:
    def test_str_input(self):
        sym, conf, reason = _extract_blocked_meta("000636")
        assert sym == "000636"
        assert reason == "blocked"

    def test_dict_input(self):
        sym, conf, reason = _extract_blocked_meta(
            {"symbol": "000636", "confidence": 0.9, "reason": "tactical=avoid"}
        )
        assert sym == "000636"
        assert conf == 0.9
        assert reason == "tactical=avoid"


# ── 4 类 row 构造 ────────────────────────────────────────────────
def _env(name: str, structured: dict, confidence: float = 0.5,
         warnings: list[str] | None = None) -> AgentOutputEnvelope:
    return AgentOutputEnvelope(
        agent_name=name,
        structured_output=structured,
        narrative_summary="",
        evidence_refs=[],
        confidence=confidence,
        warnings=warnings or [],
    )


class TestBuildMarketRow:
    def test_basic(self):
        from datetime import date
        market = _env("MarketRegimeAnalyst", {
            "market_regime": "risk_on_expansion",
            "risk_mode": "aggressive",
            "style_rotation_hint": "small_cap",
        })
        committee = _env("CommitteeSynthesizer", {
            "verdict": "long", "approved_targets": [{"symbol": "000636"}],
            "blocked_targets": [], "risk_budget": "normal",
            "rationale": "市场+板块+个股三层共振",
        }, confidence=0.75)
        row = _build_market_row(
            bdate=date(2026, 7, 3),
            window_type="close",
            market_outlook=market,
            committee_decision=committee,
            market_so=market.structured_output,
            committee_so=committee.structured_output,
            fallback_used=False,
            output_version=1,
        )
        assert row["entity_type"] == "market"
        assert row["direction"] == "long"
        assert row["strategy_type"] == STRATEGY_MARKET
        assert row["confidence"] == 0.75
        assert row["window_type"] == "close"
        assert row["horizon"] == "medium"
        # evidence 必含 2 条
        assert len(row["supporting_evidence"]) == 2
        # output_payload
        assert row["output_payload"]["verdict"] == "long"
        assert row["output_payload"]["fallback_used"] is False

    def test_fallback_marks_payload(self):
        from datetime import date
        market = _env("MarketRegimeAnalyst", {"market_regime": "neutral_mixed"})
        committee = _env("CommitteeSynthesizer", {
            "verdict": "neutral", "approved_targets": [], "blocked_targets": [],
            "risk_budget": "low", "rationale": "规则 baseline",
        }, confidence=0.4)
        row = _build_market_row(
            bdate=date(2026, 7, 3), window_type="close",
            market_outlook=market, committee_decision=committee,
            market_so=market.structured_output,
            committee_so=committee.structured_output,
            fallback_used=True, output_version=1,
        )
        assert row["output_payload"]["fallback_used"] is True

    def test_avoid_verdict_maps_to_avoid_direction(self):
        from datetime import date
        market = _env("MarketRegimeAnalyst", {"market_regime": "risk_off_panic"})
        committee = _env("CommitteeSynthesizer", {
            "verdict": "avoid", "approved_targets": [], "blocked_targets": ["X"],
            "risk_budget": "low", "rationale": "禁止新增敞口",
        }, confidence=0.9)
        row = _build_market_row(
            bdate=date(2026, 7, 3), window_type="close",
            market_outlook=market, committee_decision=committee,
            market_so=market.structured_output,
            committee_so=committee.structured_output,
            fallback_used=False, output_version=1,
        )
        assert row["direction"] == "avoid"


class TestBuildSectorRow:
    def test_mainline_sector_is_long(self):
        from datetime import date
        s = {
            "sector_id": "S1", "sector_name": "电子",
            "sector_stage": "strengthening", "mainline_flag": True,
            "sector_score": 0.85, "stage_evidence": "AI 芯片需求驱动",
        }
        row = _build_sector_row(
            bdate=date(2026, 7, 3), window_type="close",
            sector=s, market_regime="risk_on_expansion",
            fallback_used=False, output_version=1,
        )
        assert row["entity_type"] == "sector"
        assert row["direction"] == "long"
        assert row["strategy_type"] == STRATEGY_SECTOR
        assert row["confidence"] == 0.85
        assert row["output_payload"]["mainline_flag"] is True
        assert row["output_payload"]["sector_name"] == "电子"

    def test_non_mainline_sector_is_neutral(self):
        from datetime import date
        s = {"sector_id": "S2", "sector_name": "钢铁",
             "sector_stage": "watching", "mainline_flag": False,
             "sector_score": 0.3}
        row = _build_sector_row(
            bdate=date(2026, 7, 3), window_type="close",
            sector=s, market_regime="risk_on_expansion",
            fallback_used=False, output_version=1,
        )
        assert row["direction"] == "neutral"

    def test_handles_structured_output_key(self):
        from datetime import date
        s = {"structured_output": {
            "sector_id": "S1", "sector_name": "电子",
            "sector_stage": "confirmed", "mainline_flag": True,
            "sector_score": 0.7,
        }}
        row = _build_sector_row(
            bdate=date(2026, 7, 3), window_type="close",
            sector=s, market_regime="risk_on_expansion",
            fallback_used=False, output_version=1,
        )
        assert row["output_payload"]["sector_name"] == "电子"
        assert row["direction"] == "long"


class TestBuildStockRow:
    def test_approved_stock_is_long(self):
        from datetime import date
        env = _env("StockAnalyst", {
            "symbol": "000636", "trend_signal": "bullish",
            "entry_timing": "wait_pullback", "conviction_score": 0.7,
            "support_level": 12.0, "resistance_level": 18.0,
            "stop_loss_hint": 11.0, "tactical_flags": ["weak_to_strong"],
            "upstream_alignment": "aligned",
        })
        row = _build_stock_row(
            bdate=date(2026, 7, 3), window_type="close",
            symbol="000636", strategy_type=STRATEGY_APPROVED,
            direction="long", confidence=0.7, stock_env=env,
            market_regime="risk_on_expansion", invalid_conditions=[],
            fallback_used=False, output_version=1,
        )
        assert row["entity_type"] == "stock"
        assert row["direction"] == "long"
        assert row["strategy_type"] == STRATEGY_APPROVED
        assert row["output_payload"]["symbol"] == "000636"
        # evidence 2 条（committee + stock analyst）
        assert len(row["supporting_evidence"]) == 2

    def test_blocked_stock_has_invalid_conditions(self):
        from datetime import date
        row = _build_stock_row(
            bdate=date(2026, 7, 3), window_type="close",
            symbol="600519", strategy_type=STRATEGY_BLOCKED,
            direction="avoid", confidence=0.8, stock_env=None,
            market_regime="risk_off_panic",
            invalid_conditions=["tactical=avoid"],
            fallback_used=False, output_version=1,
        )
        assert row["direction"] == "avoid"
        assert row["invalid_conditions"] == ["tactical=avoid"]


# ── persist_committee_signals 入口（mock DB） ─────────────────────
class TestPersistEntry:
    @pytest.mark.asyncio
    async def test_panic_no_approved_no_blocked_only_market(self):
        """panic 模式下：committee 拦截所有 stock，只落 market 一条。"""
        from datetime import date
        market = _env("MarketRegimeAnalyst", {"market_regime": "risk_off_panic"})
        committee = _env("CommitteeSynthesizer", {
            "verdict": "avoid", "approved_targets": [],
            "blocked_targets": [{"symbol": "000636", "reason": "panic"},
                                {"symbol": "600519", "reason": "panic"}],
            "risk_budget": "low", "rationale": "禁止新增敞口",
        }, confidence=0.85)
        session = AsyncMock()
        result = await persist_committee_signals(
            session,
            business_date=date(2026, 7, 3),
            window_type="close",
            market_outlook=market,
            committee_decision=committee,
            ranked_sectors=[],
            stock_outlooks=[],
        )
        assert result.market_id != ""
        assert result.sector_count == 0
        assert result.approved_count == 0
        assert result.blocked_count == 2
        assert result.total == 3  # market + 2 blocked
        # session.execute + commit 被调
        assert session.execute.called
        assert session.commit.called

    @pytest.mark.asyncio
    async def test_full_flow_4_types(self):
        from datetime import date
        market = _env("MarketRegimeAnalyst", {"market_regime": "risk_on_expansion"})
        committee = _env("CommitteeSynthesizer", {
            "verdict": "long",
            "approved_targets": [
                {"symbol": "000636", "confidence": 0.85},
                {"symbol": "600519", "confidence": 0.7},
            ],
            "blocked_targets": [{"symbol": "999999", "reason": "tactical=avoid"}],
            "risk_budget": "normal", "rationale": "多空均衡",
        }, confidence=0.7)
        sector1 = {
            "sector_id": "S1", "sector_name": "电子",
            "sector_stage": "confirmed", "mainline_flag": True,
            "sector_score": 0.8,
        }
        sector2 = {
            "sector_id": "S2", "sector_name": "钢铁",
            "sector_stage": "watching", "mainline_flag": False,
            "sector_score": 0.3,
        }
        stock_env = _env("StockAnalyst", {
            "symbol": "000636", "trend_signal": "bullish",
            "entry_timing": "wait_pullback", "conviction_score": 0.7,
        })
        session = AsyncMock()
        result = await persist_committee_signals(
            session,
            business_date=date(2026, 7, 3),
            window_type="close",
            market_outlook=market,
            committee_decision=committee,
            ranked_sectors=[sector1, sector2],
            stock_outlooks=[stock_env],
        )
        assert result.sector_count == 2
        assert result.approved_count == 2
        assert result.blocked_count == 1
        assert result.total == 6  # market + 2 sector + 2 approved + 1 blocked

    @pytest.mark.asyncio
    async def test_empty_committee_returns_zero(self):
        from datetime import date
        market = _env("MarketRegimeAnalyst", {"market_regime": "neutral_mixed"})
        committee = _env("CommitteeSynthesizer", {
            "verdict": "neutral", "approved_targets": [],
            "blocked_targets": [], "risk_budget": "normal",
            "rationale": "无方向",
        }, confidence=0.4)
        session = AsyncMock()
        result = await persist_committee_signals(
            session,
            business_date=date(2026, 7, 3),
            window_type="close",
            market_outlook=market,
            committee_decision=committee,
            ranked_sectors=[],
            stock_outlooks=[],
        )
        # 即使 sector / approved / blocked 都空，market 一条仍写
        assert result.total == 1
        assert result.sector_count == 0
        assert result.approved_count == 0
        assert result.blocked_count == 0


# ── _row_to_dict ─────────────────────────────────────────────────
class TestRowToDict:
    def test_serializes_row(self):
        from datetime import date

        from models.outputs import FactStrategyOutput

        r = FactStrategyOutput(
            strategy_id=uuid.uuid4(),
            entity_type="market",
            entity_id=uuid.uuid4(),
            business_date=date(2026, 7, 3),
            window_type="close",
            horizon="medium",
            direction="long",
            strategy_type=STRATEGY_MARKET,
            confidence=0.7,
            supporting_evidence=[{"source": "X"}],
            invalid_conditions=[],
            output_payload={"verdict": "long"},
            output_version=1,
        )
        d = _row_to_dict(r)
        assert d["entity_type"] == "market"
        assert d["direction"] == "long"
        assert d["strategy_type"] == STRATEGY_MARKET
        assert d["business_date"] == "2026-07-03"
        assert d["output_payload"] == {"verdict": "long"}


# ── fetch_signals_latest（mock DB） ─────────────────────────────
class TestFetchSignalsLatest:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_groups(self):
        session = AsyncMock()
        # max_date query → None
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute.return_value = result_mock
        groups = await fetch_signals_latest(session)
        assert groups == {"market": [], "sector": [], "approved": [], "blocked": []}
