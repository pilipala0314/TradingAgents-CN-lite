import logging
from typing import Any

from prompts.analyst_prompts import render_sector_analyst_prompt

from agents.base import StructuredResearchAgent
from core.enums import MarketRegime, SectorStage
from schemas.agent_io import AgentOutputEnvelope, SectorStageOutput

logger = logging.getLogger(__name__)


# ── fallback 拆解的私有函数（pipeline 模式）────────────────────


def _compute_sector_score(snapshot: dict[str, Any]) -> float:
    """综合 continuation / flow_inflow / event_confirmation 三项打分（0-1）。"""
    score = 0.0
    for key in ("continuation_score", "flow_inflow_score", "event_confirmation_score"):
        try:
            score += float(snapshot.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return max(0.0, min(1.0, score / 3.0))


def _score_to_stage(score: float) -> str:
    """score → 板块阶段（threshold 0.75/0.55/0.35/0.1）。"""
    if score >= 0.75:
        return SectorStage.STRENGTHENING.value
    if score >= 0.55:
        return SectorStage.CONFIRMED.value
    if score >= 0.35:
        return SectorStage.EMERGING.value
    if score <= 0.1:
        return SectorStage.DIVERGING.value
    return SectorStage.WATCHING.value


def _apply_market_regime_constraint(
    *, stage: str, score: float, market_regime: str
) -> tuple[str, float, bool]:
    """市场风险偏好约束：risk_off → 启动期主线不入选；正常 → 高分主线入选。"""
    if market_regime in {
        MarketRegime.RISK_OFF_PANIC.value,
        MarketRegime.RISK_OFF_STABILIZED.value,
    }:
        if stage in {SectorStage.EMERGING.value, SectorStage.WATCHING.value}:
            return SectorStage.WATCHING.value, min(score, 0.3), False
        return stage, score, False
    mainline_flag = score >= 0.6 and stage in {
        SectorStage.CONFIRMED.value,
        SectorStage.STRENGTHENING.value,
    }
    return stage, score, mainline_flag


def _apply_rule_blocks(*, stage: str, mainline_flag: bool, rule_hits: list[dict[str, Any]]) -> tuple[str, bool]:
    """hard block 命中 → 强制 diverging + 取消主线资格。"""
    if any(hit.get("rule_level") == "block" for hit in rule_hits):
        return SectorStage.DIVERGING.value, False
    return stage, mainline_flag


class SectorAnalyst(StructuredResearchAgent):
    """板块生命周期分析 Agent。
    负责根据板块快照、市场阶段约束和风控规则，判断板块所处生命周期。
    """

    agent_name = "SectorAnalyst"

    def build_prompts(
        self,
        *,
        sector_snapshot: dict[str, Any],
        market_regime: str,
        rule_hits: list[dict[str, Any]],
    ) -> tuple[str, str]:
        return render_sector_analyst_prompt(sector_snapshot, market_regime, rule_hits)

    def build_narrative_summary(self, structured_data: SectorStageOutput, **kwargs: Any) -> str:
        mainline_text = "属于主线" if structured_data.mainline_flag else "不属于主线"
        return (
            f"板块处于 {structured_data.sector_stage} 阶段，"
            f"{mainline_text}，"
            f"证据：{structured_data.stage_evidence}"
        )

    def estimate_confidence(self, *, structured_data: SectorStageOutput, **kwargs: Any) -> float:
        sector_score = float(structured_data.sector_score)
        base_confidence = 0.7 + min(sector_score, 1.0) * 0.2
        if kwargs.get("market_regime") in {"risk_off_panic", "risk_off_stabilized"}:
            return min(base_confidence + 0.05, 0.95)
        return min(base_confidence, 0.9)

    def build_fallback_structured(
        self,
        *,
        output_schema: type[SectorStageOutput],
        sector_snapshot: dict[str, Any],
        market_regime: str,
        rule_hits: list[dict[str, Any]],
        **_: Any,
    ) -> SectorStageOutput:
        """LLM 不可用时直接基于板块 features + 市场约束兜底。

        Pipeline:
        1. 三项 features 平均 → score
        2. score → 阶段（threshold mapping）
        3. 市场 regime 约束（risk_off 收紧）
        4. hard block 强制 diverging
        """
        score = _compute_sector_score(sector_snapshot)
        stage = _score_to_stage(score)
        stage, score, mainline_flag = _apply_market_regime_constraint(
            stage=stage, score=score, market_regime=market_regime
        )
        stage, mainline_flag = _apply_rule_blocks(
            stage=stage, mainline_flag=mainline_flag, rule_hits=rule_hits
        )

        return SectorStageOutput(
            sector_id=str(sector_snapshot.get("sector_id") or "unknown"),
            sector_stage=stage,
            sector_score=round(score, 4),
            mainline_flag=mainline_flag,
            stage_evidence="fallback: scores + market regime + rule_hits",
        )

    async def run(
        self,
        sector_snapshot: dict[str, Any],
        market_regime: str,
        rule_hits: list[dict[str, Any]],
    ) -> AgentOutputEnvelope:
        logger.info(f"SectorAnalyst 开始分析板块: {sector_snapshot.get('sector_id', 'unknown')}")
        return await self.run_structured(
            output_schema=SectorStageOutput,
            sector_snapshot=sector_snapshot,
            market_regime=market_regime,
            rule_hits=rule_hits,
        )


class SectorRankingSynthesizer:
    """板块排序综合器。
    汇总所有 SectorAnalyst 的输出，结合市场环境，选出 Top N 板块。
    """

    async def run(
        self,
        market_regime_envelope: AgentOutputEnvelope,
        sector_envelopes: list[AgentOutputEnvelope],
    ) -> list[dict[str, Any]]:
        logger.info(f"SectorRankingSynthesizer 开始排序 {len(sector_envelopes)} 个板块...")

        valid_sectors: list[dict[str, Any]] = []
        market_regime = market_regime_envelope.structured_output.get("market_regime", "")
        for env in sector_envelopes:
            stage = env.structured_output.get("sector_stage", "watching")
            score = float(env.structured_output.get("sector_score", 0.0))
            mainline_flag = bool(env.structured_output.get("mainline_flag", False))

            # 市场差时收紧准入；市场正常时允许启动期主线进入候选。
            if market_regime in ["risk_off_panic", "risk_off_stabilized"]:
                if stage not in ["confirmed", "strengthening"]:
                    continue
            elif stage not in ["emerging", "confirmed", "strengthening"]:
                continue

            adjusted_score = score
            if mainline_flag:
                adjusted_score += 0.08
            if stage == "strengthening":
                adjusted_score += 0.05
            if stage == "diverging":
                adjusted_score -= 0.08

            valid_sectors.append(
                {
                    "sector_id": env.structured_output.get("sector_id", "unknown"),
                    "sector_stage": stage,
                    "score": round(adjusted_score, 4),
                    "mainline_flag": mainline_flag,
                    "summary": env.narrative_summary,
                }
            )

        valid_sectors.sort(key=lambda x: x["score"], reverse=True)
        return valid_sectors[:5]
