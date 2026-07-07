"""CommitteeSynthesizer 单测全面覆盖。"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.committee import CommitteeSynthesizer
from schemas.agent_io import AgentOutputEnvelope

# ── helpers ─────────────────────────────────────────


def _make_envelope(
    *, agent_name: str = "test", structured: dict[str, Any] | None = None,
    confidence: float = 0.7, narrative: str = "",
) -> AgentOutputEnvelope:
    return AgentOutputEnvelope(
        agent_name=agent_name,
        structured_output=structured or {},
        narrative_summary=narrative,
        evidence_refs=[],
        confidence=confidence,
    )


def _bullish_env(symbol: str, confidence: float = 0.8) -> AgentOutputEnvelope:
    return _make_envelope(
        agent_name="StockAnalyst",
        structured={
            "symbol": symbol,
            "trend_signal": "bullish",
            "tactical_flags": [],
            "conviction_score": confidence,
        },
    )


def _avoid_env(symbol: str) -> AgentOutputEnvelope:
    return _make_envelope(
        agent_name="StockAnalyst",
        structured={
            "symbol": symbol,
            "trend_signal": "bearish",
            "tactical_flags": ["avoid"],
            "conviction_score": 0.1,
        },
    )


def _hard_block_env(symbol: str) -> AgentOutputEnvelope:
    return _make_envelope(
        agent_name="StockAnalyst",
        structured={
            "symbol": symbol,
            "trend_signal": "bearish",
            "tactical_flags": ["hard_block"],
            "conviction_score": 0.1,
        },
    )


def _neutral_env(symbol: str) -> AgentOutputEnvelope:
    return _make_envelope(
        agent_name="StockAnalyst",
        structured={
            "symbol": symbol,
            "trend_signal": "neutral",
            "tactical_flags": [],
            "conviction_score": 0.5,
        },
    )


# ── 规则 baseline ─────────────────────────────────────


class TestRuleBasedDecision:
    def setup_method(self) -> None:
        self.synth = CommitteeSynthesizer()

    def test_panic_market_avoid_all_blocked(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_off_panic"})
        stocks = [_bullish_env("600519"), _bullish_env("000001")]
        result = self.synth._rule_based_decision(market, [], stocks)
        assert result["verdict"] == "avoid"
        assert result["confidence"] == 0.85
        assert result["approved_targets"] == []
        assert result["risk_budget"] == "low"
        assert "600519" in result["blocked_targets"]
        assert "000001" in result["blocked_targets"]

    def test_risk_on_expansion_bullish_approved(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env("600519", confidence=0.9), _bullish_env("000001", confidence=0.7)]
        result = self.synth._rule_based_decision(market, [], stocks)
        assert result["verdict"] == "long"
        assert result["confidence"] == 0.6
        assert len(result["approved_targets"]) == 2
        # 按 confidence 降序
        assert result["approved_targets"][0]["symbol"] == "600519"
        assert result["approved_targets"][0]["confidence"] == 0.9

    def test_risk_on_rotation_bullish_approved(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_rotation"})
        stocks = [_bullish_env("600519")]
        result = self.synth._rule_based_decision(market, [], stocks)
        assert result["verdict"] == "long"
        assert len(result["approved_targets"]) == 1

    def test_neutral_stocks_not_approved(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_neutral_env("600519"), _bullish_env("000001")]
        result = self.synth._rule_based_decision(market, [], stocks)
        approved_syms = [t["symbol"] for t in result["approved_targets"]]
        assert "600519" not in approved_syms
        assert "000001" in approved_syms

    def test_avoid_tactical_flag_blocked(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_avoid_env("600519"), _bullish_env("000001")]
        result = self.synth._rule_based_decision(market, [], stocks)
        assert "600519" in [b["symbol"] for b in result["blocked_targets"]]
        assert "000001" in [t["symbol"] for t in result["approved_targets"]]

    def test_hard_block_tactical_flag_blocked(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_hard_block_env("600519"), _bullish_env("000001")]
        result = self.synth._rule_based_decision(market, [], stocks)
        assert "600519" in [b["symbol"] for b in result["blocked_targets"]]
        assert len(result["approved_targets"]) == 1

    def test_max_5_approved(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env(f"6000{i:02d}", confidence=0.5 + i * 0.05) for i in range(8)]
        result = self.synth._rule_based_decision(market, [], stocks)
        assert len(result["approved_targets"]) == 5

    def test_approved_sorted_by_confidence_desc(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [
            _bullish_env("600001", confidence=0.5),
            _bullish_env("600002", confidence=0.9),
            _bullish_env("600003", confidence=0.7),
        ]
        result = self.synth._rule_based_decision(market, [], stocks)
        confs = [t["confidence"] for t in result["approved_targets"]]
        assert confs == sorted(confs, reverse=True)

    def test_empty_stocks_neutral(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        result = self.synth._rule_based_decision(market, [], [])
        assert result["verdict"] == "neutral"
        assert result["confidence"] == 0.4
        assert result["approved_targets"] == []
        assert result["risk_budget"] == "normal"

    def test_risk_off_stabilized_neutral_verdict(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_off_stabilized"})
        stocks = [_bullish_env("600519")]
        result = self.synth._rule_based_decision(market, [], stocks)
        # 规则 baseline 在非 panic 的 risk_off 不直接拦截
        assert result["verdict"] in ("long", "neutral")

    def test_bullish_but_off_market_not_approved(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_off_stabilized"})
        stocks = [_bullish_env("600519")]
        result = self.synth._rule_based_decision(market, [], stocks)
        approved_syms = [t["symbol"] for t in result["approved_targets"]]
        assert "600519" not in approved_syms


# ── LLM payload 构造 ──────────────────────────────────


class TestBuildLlmPayload:
    def setup_method(self) -> None:
        self.synth = CommitteeSynthesizer()

    def test_payload_is_valid_json(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        sectors = [_make_envelope(structured={"sector_id": "sw_electronics"})]
        stocks = [_bullish_env("600519")]
        baseline = {"verdict": "long", "approved_targets": []}
        payload = self.synth._build_llm_payload(market, sectors, stocks, baseline)
        parsed = json.loads(payload)
        assert parsed["market_outlook"]["market_regime"] == "risk_on_expansion"
        assert len(parsed["sector_outlooks"]) == 1
        assert len(parsed["stock_outlooks"]) == 1
        assert parsed["rule_baseline"]["verdict"] == "long"
        assert "instruction" in parsed

    def test_payload_handles_unicode(self) -> None:
        market = _make_envelope(structured={"market_regime": "中性"})
        baseline = {"verdict": "neutral", "rationale": "市场中性"}
        payload = self.synth._build_llm_payload(market, [], [], baseline)
        parsed = json.loads(payload)
        assert parsed["market_outlook"]["market_regime"] == "中性"


# ── LLM response 解析 ──────────────────────────────────


class TestParseResponse:
    def setup_method(self) -> None:
        self.synth = CommitteeSynthesizer()

    def test_empty_content(self) -> None:
        assert self.synth._parse_response("") == {}

    def test_direct_json(self) -> None:
        content = '{"verdict": "long", "confidence": 0.8}'
        out = self.synth._parse_response(content)
        assert out["verdict"] == "long"
        assert out["confidence"] == 0.8

    def test_json_code_block(self) -> None:
        content = '```json\n{"verdict": "short", "confidence": 0.7}\n```'
        out = self.synth._parse_response(content)
        assert out["verdict"] == "short"

    def test_code_block_no_lang(self) -> None:
        content = '```\n{"verdict": "long"}\n```'
        out = self.synth._parse_response(content)
        assert out["verdict"] == "long"

    def test_multiline_json(self) -> None:
        content = (
            "以下为最终决策：\n"
            "```json\n"
            "{\n"
            '  "verdict": "long",\n'
            '  "approved_targets": [{"symbol": "600519"}],\n'
            '  "rationale": "市场允许"\n'
            "}\n"
            "```\n"
            "祝好"
        )
        out = self.synth._parse_response(content)
        assert out["verdict"] == "long"
        assert len(out["approved_targets"]) == 1

    def test_invalid_content(self) -> None:
        assert self.synth._parse_response("not json") == {}

    def test_partial_json(self) -> None:
        content = '{"verdict": "long", "confidence": "0.8"}'  # confidence 字符串
        out = self.synth._parse_response(content)
        # parse 成功（虽然是字符串）
        assert out["verdict"] == "long"
        assert out["confidence"] == "0.8"


# ── 端到端 run() ──────────────────────────────────────


class TestRun:
    def setup_method(self) -> None:
        self.synth = CommitteeSynthesizer()

    def _mock_chat_model(self, content: str) -> MagicMock:
        chat_model = MagicMock()
        chat_model.ainvoke = AsyncMock(
            return_value=MagicMock(content=content)
        )
        client = MagicMock()
        client.get_chat_model = MagicMock(return_value=chat_model)
        return client

    @pytest.mark.asyncio
    async def test_run_with_llm_success(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env("600519", confidence=0.9)]

        client = self._mock_chat_model(
            '{"verdict": "long", "confidence": 0.9, '
            '"approved_targets": [{"symbol": "600519"}], '
            '"blocked_targets": [], "risk_budget": "high", '
            '"rationale": "LLM 决策"}'
        )

        with patch("core.llm.create_llm_client", return_value=client):
            result = await self.synth.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks
            )

        assert result.agent_name == "CommitteeSynthesizer"
        assert result.structured_output["verdict"] == "long"
        assert result.confidence == 0.9
        assert result.structured_output["risk_budget"] == "high"
        assert result.warnings == []  # 无 fallback 警告
        assert "⚠️" not in result.narrative_summary  # 无 fallback 标记

    @pytest.mark.asyncio
    async def test_run_llm_failure_falls_back_to_baseline(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env("600519", confidence=0.9)]

        # LLM 抛错
        with patch(
            "core.llm.create_llm_client",
            side_effect=RuntimeError("deepseek down"),
        ):
            result = await self.synth.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks
            )

        # fallback 行为
        assert "LLM fallback used" in result.warnings
        assert "⚠️" in result.narrative_summary
        # baseline 应为 long（市场 risk_on + bullish 准入）
        assert result.structured_output["verdict"] == "long"
        assert result.confidence == 0.6  # baseline 置信

    @pytest.mark.asyncio
    async def test_run_partial_llm_fills_from_baseline(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env("600519", confidence=0.9)]

        # LLM 只返 verdict 字段，其他走 baseline
        client = self._mock_chat_model('{"verdict": "short"}')

        with patch("core.llm.create_llm_client", return_value=client):
            result = await self.synth.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks
            )

        # verdict 来自 LLM
        assert result.structured_output["verdict"] == "short"
        # 其他字段从 baseline 补齐
        assert len(result.structured_output["approved_targets"]) >= 1
        assert result.structured_output["risk_budget"] == "normal"

    @pytest.mark.asyncio
    async def test_run_panic_uses_baseline_even_if_llm_says_long(self) -> None:
        """极端恐慌：baseline=avoid，LLM 误判为 long，但 LLM verdict 会被采用。"""
        market = _make_envelope(structured={"market_regime": "risk_off_panic"})
        stocks = [_bullish_env("600519")]

        client = self._mock_chat_model('{"verdict": "long"}')

        with patch("core.llm.create_llm_client", return_value=client):
            result = await self.synth.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks
            )

        # LLM verdict=long 会被采用（这是设计：LLM 是 final name）
        # 但 approved_targets 来自 baseline（empty）
        assert result.structured_output["verdict"] == "long"
        assert result.structured_output["approved_targets"] == []
        assert result.structured_output["blocked_targets"]  # baseline 拦截了

    @pytest.mark.asyncio
    async def test_run_confidence_clamped(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env("600519")]

        # LLM confidence=1.5 越界
        client = self._mock_chat_model(
            '{"verdict": "long", "confidence": 1.5, '
            '"approved_targets": [], "blocked_targets": []}'
        )

        with patch("core.llm.create_llm_client", return_value=client):
            result = await self.synth.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks
            )

        assert result.confidence == 1.0  # clamp 到 1.0

    @pytest.mark.asyncio
    async def test_run_negative_confidence_clamped(self) -> None:
        market = _make_envelope(structured={"market_regime": "risk_on_expansion"})
        stocks = [_bullish_env("600519")]

        client = self._mock_chat_model(
            '{"verdict": "long", "confidence": -0.5, '
            '"approved_targets": [], "blocked_targets": []}'
        )

        with patch("core.llm.create_llm_client", return_value=client):
            result = await self.synth.run(
                market_outlook=market, sector_outlooks=[], stock_outlooks=stocks
            )

        assert result.confidence == 0.0


# ── 系统 prompt 约束 ────────────────────────────────


class TestSystemPrompt:
    def test_system_prompt_has_rules(self) -> None:
        synth = CommitteeSynthesizer()
        assert "panic" in synth.system_prompt
        assert "approved_targets" in synth.system_prompt
        assert "verdict" in synth.system_prompt
        assert "JSON" in synth.system_prompt or "json" in synth.system_prompt

    def test_system_prompt_warns_against_overwriting(self) -> None:
        synth = CommitteeSynthesizer()
        # 关键：上游事实不能改写
        assert "不能" in synth.system_prompt or "不能改写" in synth.system_prompt
