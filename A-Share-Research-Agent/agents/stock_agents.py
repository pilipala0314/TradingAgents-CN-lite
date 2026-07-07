import logging
from typing import Any

from prompts.analyst_prompts import render_stock_analyst_prompt

from agents.base import StructuredResearchAgent
from core.enums import MarketRegime, SectorStage
from schemas.agent_io import AgentOutputEnvelope, StockTacticalOutput

logger = logging.getLogger(__name__)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


# ── fallback 拆解的私有函数（pipeline 模式）────────────────────


def _extract_features(snapshot: dict[str, Any]) -> dict[str, Any]:
    """从原始 snapshot 提取并归一所有判定所需字段。"""
    return {
        "rs": _coerce_float(snapshot.get("relative_strength_score"), 0.5),
        "crowd": _coerce_float(snapshot.get("crowding_score"), 0.5),
        "famous_buy": bool(snapshot.get("lhb_famous_buy_flag")),
        "limit_up_to_down": bool(snapshot.get("limit_up_to_limit_down")),
        "limit_down_to_up": bool(snapshot.get("limit_down_to_limit_up")),
        "a_kill": bool(snapshot.get("a_kill")),
        "announcement_risk": _coerce_float(snapshot.get("announcement_risk_score"), 0.0),
        "valuation_percentile": _coerce_float(snapshot.get("valuation_percentile"), 0.5),
        "support": _coerce_float(snapshot.get("support_level"), 0.0),
        "resistance": _coerce_float(snapshot.get("resistance_level"), 0.0),
        "stop_loss": _coerce_float(snapshot.get("stop_loss_hint"), 0.0),
    }


def _has_hard_block(rule_hits: list[dict[str, Any]]) -> bool:
    return "block" in {str(hit.get("rule_level")) for hit in rule_hits}


def _signal_for_hard_block() -> dict[str, Any]:
    """hard block 命中 → 一票否决。"""
    return {
        "trend_signal": "bearish",
        "entry_timing": "avoid",
        "risk_mode": "avoid",
        "position_role": "laggard",
        "conviction": 0.1,
    }


def _signal_for_a_kill_or_limit_flip() -> dict[str, Any]:
    """一字跌停 / 涨停转跌停 形态 → 高优先级看空。"""
    return {
        "trend_signal": "bearish",
        "entry_timing": "avoid",
        "risk_mode": "avoid",
        "position_role": "laggard",
        "conviction": 0.15,
    }


def _signal_for_panic_market() -> dict[str, Any]:
    """市场 panic → 整体降级。"""
    return {
        "trend_signal": "bearish",
        "entry_timing": "avoid",
        "risk_mode": "avoid",
        "position_role": "laggard",
        "conviction": 0.2,
    }


def _signal_for_decaying_sector() -> dict[str, Any]:
    """板块 decaying → 防御性看空。"""
    return {
        "trend_signal": "bearish",
        "entry_timing": "avoid",
        "risk_mode": "defensive",
        "position_role": "laggard",
        "conviction": 0.3,
    }


def _signal_for_diverging_sector() -> dict[str, Any]:
    """板块 diverging → 等待回调。"""
    return {
        "trend_signal": "neutral",
        "entry_timing": "wait_pullback",
        "risk_mode": "defensive",
        "position_role": "follower",
        "conviction": 0.45,
    }


def _signal_for_strong_setup(
    *, market_regime: str, rs: float
) -> dict[str, Any]:
    """rs>=0.65 + 龙虎榜净买入 + 拥挤度<0.7 + 板块 confirmed/strengthening。"""
    if market_regime in {
        MarketRegime.RISK_ON_EXPANSION.value,
        MarketRegime.RISK_ON_ROTATION.value,
    }:
        entry_timing = "now"
    else:
        entry_timing = "wait_breakout"
    return {
        "trend_signal": "bullish",
        "entry_timing": entry_timing,
        "risk_mode": "aggressive",
        "position_role": "leader",
        "conviction": min(0.7 + rs * 0.2, 0.9),
    }


def _signal_for_moderate_setup(market_regime: str) -> dict[str, Any]:
    """rs>=0.55 但非 leader 配置。"""
    return {
        "trend_signal": "bullish" if market_regime != MarketRegime.NEUTRAL_MIXED.value else "neutral",
        "entry_timing": "wait_pullback",
        "risk_mode": "balanced",
        "position_role": "follower",
        "conviction": 0.55,
    }


def _signal_for_default_setup() -> dict[str, Any]:
    """其余情况 → 中性等待。"""
    return {
        "trend_signal": "neutral",
        "entry_timing": "wait_breakout",
        "risk_mode": "balanced",
        "position_role": "follower",
        "conviction": 0.4,
    }


def _dispatch_signal(
    *, features: dict[str, Any], market_regime: str, sector_stage: str
) -> dict[str, Any]:
    """按优先级路由到对应场景的 signal。"""
    if features["a_kill"] or features["limit_up_to_down"]:
        return _signal_for_a_kill_or_limit_flip()
    if market_regime == MarketRegime.RISK_OFF_PANIC.value:
        return _signal_for_panic_market()
    if sector_stage == SectorStage.DECAYING.value:
        return _signal_for_decaying_sector()
    if sector_stage == SectorStage.DIVERGING.value:
        return _signal_for_diverging_sector()
    if (
        features["rs"] >= 0.65
        and features["famous_buy"]
        and features["crowd"] < 0.7
        and sector_stage
        in {SectorStage.STRENGTHENING.value, SectorStage.CONFIRMED.value}
    ):
        return _signal_for_strong_setup(market_regime=market_regime, rs=features["rs"])
    if features["rs"] >= 0.55:
        return _signal_for_moderate_setup(market_regime)
    return _signal_for_default_setup()


def _apply_risk_adjustments(
    signal: dict[str, Any], features: dict[str, Any]
) -> dict[str, Any]:
    """公告风险 / 估值高分位 → 降权。"""
    if features["announcement_risk"] >= 0.7:
        signal["conviction"] = max(signal["conviction"] - 0.2, 0.1)
        if signal["trend_signal"] == "bullish":
            signal["entry_timing"] = "wait_pullback"
    if (
        features["valuation_percentile"] >= 0.85
        and signal["trend_signal"] == "bullish"
    ):
        signal["entry_timing"] = "wait_pullback"
        signal["risk_mode"] = "defensive"
    return signal


def _derive_upstream_alignment(
    *, trend_signal: str, market_regime: str, sector_stage: str, risk_mode: str
) -> str:
    if (
        market_regime == MarketRegime.RISK_OFF_PANIC.value
        or risk_mode == "avoid"
        or (
            sector_stage
            in {
                SectorStage.WATCHING.value,
                SectorStage.DECAYING.value,
                SectorStage.DIVERGING.value,
            }
            and trend_signal == "bullish"
        )
    ):
        return "conflicting"
    if trend_signal == "neutral":
        return "neutral"
    return "aligned"


def _derive_price_levels(features: dict[str, Any]) -> tuple[float, float, float]:
    support, resistance, stop_loss = (
        features["support"],
        features["resistance"],
        features["stop_loss"],
    )
    if support <= 0.0 and resistance > 0.0:
        support = round(resistance * 0.92, 2)
    if resistance <= 0.0 and support > 0.0:
        resistance = round(support * 1.08, 2)
    if stop_loss <= 0.0 and support > 0.0:
        stop_loss = round(support * 0.97, 2)
    return round(support, 2), round(resistance, 2), round(stop_loss, 2)


def _derive_tactical_flags(snapshot: dict[str, Any]) -> list[str]:
    flag_keys = (
        "streak_break",
        "first_yin_after_streak",
        "reversal_board",
        "weak_to_strong",
        "a_kill",
        "limit_up_to_limit_down",
        "limit_down_to_limit_up",
        "laggard_rise",
        "position_steal",
        "lhb_listed",
        "lhb_famous_buy_flag",
    )
    return [k for k in flag_keys if snapshot.get(k)]


class StockAnalyst(StructuredResearchAgent):
    """个股战术分析 Agent。

    接收个股特征快照 + 上位市场/板块约束 + 个股级风控命中，
    输出可执行的战术结论（趋势方向、入场时机、关键位、止损、置信度）。
    """

    agent_name = "StockAnalyst"

    def build_prompts(
        self,
        *,
        stock_snapshot: dict[str, Any],
        market_regime: str,
        sector_stage: str,
        rule_hits: list[dict[str, Any]],
    ) -> tuple[str, str]:
        return render_stock_analyst_prompt(
            stock_snapshot=stock_snapshot,
            market_regime=market_regime,
            sector_stage=sector_stage,
            rule_hits=rule_hits,
        )

    def build_narrative_summary(self, structured_data: StockTacticalOutput, **kwargs: Any) -> str:
        flags = "、".join(structured_data.tactical_flags) if structured_data.tactical_flags else "无"
        return (
            f"个股战术结论：趋势 {structured_data.trend_signal}，"
            f"入场 {structured_data.entry_timing}，"
            f"风险 {structured_data.risk_mode}，"
            f"板块角色 {structured_data.position_role}，"
            f"置信度 {structured_data.conviction_score:.2f}。\n"
            f"支撑 {structured_data.support_level:.2f} / 阻力 {structured_data.resistance_level:.2f} / 止损 {structured_data.stop_loss_hint:.2f}。\n"
            f"战术标签：{flags}。\n"
            f"上位契合度：{structured_data.upstream_alignment}。\n"
            f"核心证据：{structured_data.key_evidence}"
        )

    def estimate_confidence(self, *, structured_data: StockTacticalOutput, **kwargs: Any) -> float:
        # 置信度直接采用结构化字段；父类评分仅作 envelope 顶层冗余。
        return round(float(structured_data.conviction_score), 4)

    def build_fallback_structured(
        self,
        *,
        output_schema: type[StockTacticalOutput],
        stock_snapshot: dict[str, Any],
        market_regime: str,
        sector_stage: str,
        rule_hits: list[dict[str, Any]],
        **_: Any,
    ) -> StockTacticalOutput:
        """LLM 不可用时基于特征 + 上位约束兜底。

        Pipeline:
        1. hard block 一票否决
        2. 优先级：a_kill/limit_flip > panic > decaying > diverging > strong setup > moderate > default
        3. 风险调整：公告风险 / 估值高分位
        4. 上位契合度
        5. 价格位（缺失时由阻力/支撑反推）
        """
        features = _extract_features(stock_snapshot)
        if _has_hard_block(rule_hits):
            signal = _signal_for_hard_block()
        else:
            signal = _dispatch_signal(
                features=features, market_regime=market_regime, sector_stage=sector_stage
            )
            signal = _apply_risk_adjustments(signal, features)

        upstream_alignment = _derive_upstream_alignment(
            trend_signal=signal["trend_signal"],
            market_regime=market_regime,
            sector_stage=sector_stage,
            risk_mode=signal["risk_mode"],
        )
        support, resistance, stop_loss = _derive_price_levels(features)

        flags = _derive_tactical_flags(stock_snapshot)
        if _has_hard_block(rule_hits) and not flags:
            flags.append("hard_block_triggered")

        return StockTacticalOutput(
            trend_signal=signal["trend_signal"],
            entry_timing=signal["entry_timing"],
            risk_mode=signal["risk_mode"],
            position_role=signal["position_role"],
            conviction_score=round(signal["conviction"], 4),
            support_level=support,
            resistance_level=resistance,
            stop_loss_hint=stop_loss,
            tactical_flags=flags,
            key_evidence="fallback: 上位约束 + 相对强度 + 龙虎榜/拥挤度/公告风险 综合判定。",
            upstream_alignment=upstream_alignment,
        )

    async def run(
        self,
        stock_snapshot: dict[str, Any],
        market_regime: str,
        sector_stage: str,
        rule_hits: list[dict[str, Any]],
    ) -> AgentOutputEnvelope:
        logger.info(
            "StockAnalyst 开始分析个股: %s (market=%s, sector=%s)",
            stock_snapshot.get("symbol", "unknown"),
            market_regime,
            sector_stage,
        )
        return await self.run_structured(
            output_schema=StockTacticalOutput,
            stock_snapshot=stock_snapshot,
            market_regime=market_regime,
            sector_stage=sector_stage,
            rule_hits=rule_hits,
        )
