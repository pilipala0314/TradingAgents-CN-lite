"""test_committee_and_renderer — 验证 Committee 规则 baseline + ReportRenderer markdown 渲染。"""
from __future__ import annotations

import pytest

from agents.committee import CommitteeSynthesizer
from agents.report_renderer import ReportRenderer
from schemas.agent_io import AgentOutputEnvelope


def _env(agent_name: str, structured: dict, confidence: float = 0.5) -> AgentOutputEnvelope:
    return AgentOutputEnvelope(
        agent_name=agent_name,
        structured_output=structured,
        narrative_summary="",
        evidence_refs=[],
        confidence=confidence,
    )


class TestCommitteeRuleBaseline:
    def test_panic_regime_short_circuits_to_avoid(self):
        market = _env("MarketRegimeAnalyst", {"market_regime": "risk_off_panic"})
        decision = CommitteeSynthesizer()._rule_based_decision(market, [], [])
        assert decision["verdict"] == "avoid"
        assert decision["confidence"] >= 0.8
        assert decision["risk_budget"] == "low"

    def test_bullish_stock_in_risk_on_gets_approved(self):
        market = _env("MarketRegimeAnalyst", {"market_regime": "risk_on_expansion"})
        stocks = [
            _env("StockAnalyst", {"symbol": "000636", "trend_signal": "bullish", "tactical_flags": ["weak_to_strong"], "confidence": 0.7}),
            _env("StockAnalyst", {"symbol": "600519", "trend_signal": "bearish", "tactical_flags": ["a_kill"], "confidence": 0.3}),
        ]
        decision = CommitteeSynthesizer()._rule_based_decision(market, [], stocks)
        assert decision["verdict"] == "long"
        # 000636 bullish 在 risk_on 应被准入
        assert any(t["symbol"] == "000636" for t in decision["approved_targets"])
        # 600519 bearish 不在准入集
        assert not any(t["symbol"] == "600519" for t in decision["approved_targets"])

    def test_max_5_approved_sorted_by_confidence(self):
        market = _env("MarketRegimeAnalyst", {"market_regime": "risk_on_expansion"})
        stocks = [
            _env("StockAnalyst", {"symbol": f"00000{i}", "trend_signal": "bullish", "confidence": 0.5 + i * 0.05})
            for i in range(8)
        ]
        decision = CommitteeSynthesizer()._rule_based_decision(market, [], stocks)
        assert len(decision["approved_targets"]) == 5
        # 排序按 confidence 降序
        confs = [t["confidence"] for t in decision["approved_targets"]]
        assert confs == sorted(confs, reverse=True)


class TestCommitteeRunFallback:
    @pytest.mark.asyncio
    async def test_fallback_when_llm_unavailable(self, monkeypatch):
        from core import llm

        def boom(**kwargs):
            raise RuntimeError("LLM API key not set")

        monkeypatch.setattr(llm, "create_llm_client", boom)

        market = _env("MarketRegimeAnalyst", {"market_regime": "risk_on_expansion"})
        stocks = [_env("StockAnalyst", {"symbol": "000636", "trend_signal": "bullish", "tactical_flags": [], "confidence": 0.7})]
        envelope = await CommitteeSynthesizer().run(
            market_outlook=market, sector_outlooks=[], stock_outlooks=stocks
        )
        assert envelope.agent_name == "CommitteeSynthesizer"
        assert "LLM fallback used" in envelope.warnings


class TestReportRenderer:
    def test_renders_markdown_with_sections(self):
        market = _env("MarketRegimeAnalyst", {"market_regime": "risk_on_rotation", "risk_appetite": "high", "liquidity": "ample", "key_factors": ["PBoC rate cut", "Tech rebound"]})
        sectors = [
            _env("SectorAnalyst", {"sector_id": "S1", "sector_name": "电子", "sector_stage": "strengthening", "strength_score": 0.85, "one_liner": "AI chip demand drives rally"}),
        ]
        stocks = [
            _env("StockAnalyst", {"symbol": "000636", "trend_signal": "bullish", "tactical_flags": ["weak_to_strong"], "confidence": 0.7, "one_liner": "MLCC price up cycle"}),
        ]
        committee = _env("CommitteeSynthesizer", {"verdict": "long", "approved_targets": [{"symbol": "000636", "trend": "bullish", "confidence": 0.7}], "blocked_targets": [], "risk_budget": "normal", "rationale": "市场+板块+个股三层共振"}, confidence=0.7)
        md = ReportRenderer().render(
            business_date="2026-07-01",
            window_type="close",
            market_outlook=market,
            sector_outlooks=sectors,
            stock_outlooks=stocks,
            committee_decision=committee,
        )
        assert "A 股投研日报" in md
        assert "市场层" in md
        assert "板块层" in md
        assert "个股层" in md
        assert "委员会最终决策" in md
        assert "000636" in md
        assert "投资建议" in md
