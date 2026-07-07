import logging
from typing import Any

from prompts.analyst_prompts import render_market_analyst_prompt

from agents.base import StructuredResearchAgent
from core.enums import MarketRegime
from schemas.agent_io import AgentOutputEnvelope, MarketRegimeOutput

logger = logging.getLogger(__name__)


# ── fallback 拆解的私有函数 ─────────────────────


def _derive_risk_mode(*, regime: str, rule_hits: list[dict[str, Any]]) -> str:
    if any(hit.get("rule_level") == "block" for hit in rule_hits):
        return "avoid"
    if regime in {
        MarketRegime.RISK_OFF_PANIC.value,
        MarketRegime.RISK_OFF_STABILIZED.value,
    }:
        return "defensive"
    if regime in {
        MarketRegime.RISK_ON_EXPANSION.value,
        MarketRegime.RISK_ON_ROTATION.value,
    }:
        return "aggressive"
    return "balanced"


def _derive_style_hint(snapshot: dict[str, Any]) -> str:
    raw = str(snapshot.get("style_bias") or "")
    if raw in {"large_cap", "theme", "value", "growth"}:
        return raw
    return "no_bias"


class MarketRegimeAnalyst(StructuredResearchAgent):
    """市场阶段分析 Agent。
    负责根据全市场快照和风控命中情况，产出结构化的市场阶段判断。
    """

    agent_name = "MarketRegimeAnalyst"

    def build_prompts(
        self,
        *,
        market_snapshot: dict[str, Any],
        rule_hits: list[dict[str, Any]],
    ) -> tuple[str, str]:
        return render_market_analyst_prompt(market_snapshot, rule_hits)

    def build_narrative_summary(self, structured_data: MarketRegimeOutput, **kwargs: Any) -> str:
        return (
            f"当前市场判定为 {structured_data.market_regime}，"
            f"风险模式：{structured_data.risk_mode}，"
            f"风格提示：{structured_data.style_rotation_hint}。"
        )

    def estimate_confidence(self, *, structured_data: MarketRegimeOutput, **kwargs: Any) -> float:
        rule_hits = kwargs.get("rule_hits") or []
        if any(hit.get("rule_level") == "block" for hit in rule_hits):
            return 0.95
        return 0.88

    def build_fallback_structured(
        self,
        *,
        output_schema: type[MarketRegimeOutput],
        market_snapshot: dict[str, Any],
        rule_hits: list[dict[str, Any]],
        **_: Any,
    ) -> MarketRegimeOutput:
        """LLM 不可用时直接基于 features/rules 兜底。"""
        # market_regime 来自 features_engine 的真实状态标签，必须严格使用枚举值。
        regime = str(market_snapshot.get("market_regime") or MarketRegime.NEUTRAL_MIXED.value)
        risk_mode = _derive_risk_mode(regime=regime, rule_hits=rule_hits)
        style_hint = _derive_style_hint(market_snapshot)

        return MarketRegimeOutput(
            market_regime=regime,
            risk_mode=risk_mode,
            style_rotation_hint=style_hint,
            state_support_mode=str(market_snapshot.get("state_support_mode") or "none"),
        )

    async def run(
        self,
        market_snapshot: dict[str, Any],
        rule_hits: list[dict[str, Any]],
    ) -> AgentOutputEnvelope:
        logger.info("MarketRegimeAnalyst 开始分析...")
        return await self.run_structured(
            output_schema=MarketRegimeOutput,
            market_snapshot=market_snapshot,
            rule_hits=rule_hits,
        )
