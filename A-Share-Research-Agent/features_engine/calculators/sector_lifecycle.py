"""sector_lifecycle — 单板块生命周期特征计算。

设计目标：把"per-sector"计算职责封装成单一函数 `calculate_sector_lifecycle_features`，
供 `data_pipeline/etl/sector_feature.py` 在循环中逐板块调用。

与 `SectorFeatureCalculator.build_sector_snapshot` 的差异：
- `build_sector_snapshot` 接全市场 `sector_df` 一次性算（用于全量场景）
- `calculate_sector_lifecycle_features` 接**单个板块**的事实数据，返回**单条** dict（用于
  ETL 逐板块落库 + 失败容忍场景）
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from core.enums import MarketRegime, SectorStage

logger = logging.getLogger(__name__)


def calculate_sector_lifecycle_features(
    *,
    sector_id: Any,
    sector_name: str,
    sector_type: str,
    business_date: date,
    market_snapshot: dict[str, Any],
    bar_df: pd.DataFrame,
    flow_df: pd.DataFrame,
    leader_df: pd.DataFrame,
    event_payload: dict[str, Any] | list[dict[str, Any]] | None = None,
    feature_version: int = 1,
) -> dict[str, Any]:
    """单板块生命周期特征 — 输出 fact_sector_snapshot 一行可写入 dict。

    输入约定（薄壳要求）：
        - bar_df       板块指数日线，至少含 close / turnover / pct_change 列
        - flow_df      板块资金流，至少含 net_inflow / main_net_inflow 列
        - leader_df    板块领涨股列表，至少含 code / name / change_pct
        - event_payload 板块事件 dict 或 list[dict]（来自 AkShareClient.get_sector_event_signals）
    """
    market_regime = str(market_snapshot.get("market_regime") or MarketRegime.NEUTRAL_MIXED.value)

    # 1) 行情指标
    sector_turnover = float(bar_df["turnover"].sum()) if not bar_df.empty and "turnover" in bar_df.columns else 0.0
    sector_return = float(bar_df["pct_change"].iloc[-1]) if not bar_df.empty and "pct_change" in bar_df.columns and len(bar_df) else 0.0
    rising_count = int((bar_df["pct_change"] > 0).sum()) if not bar_df.empty and "pct_change" in bar_df.columns else 0
    component_count = len(bar_df) if not bar_df.empty else 0

    # 2) 资金流指标
    net_flow = float(flow_df["net_inflow"].sum()) if not flow_df.empty and "net_inflow" in flow_df.columns else 0.0
    main_net_flow = float(flow_df["main_net_inflow"].sum()) if not flow_df.empty and "main_net_inflow" in flow_df.columns else 0.0
    net_flow_acceleration = float(market_snapshot.get("net_flow_acceleration", 0.0))

    # 3) 领涨股
    leader_change_pct = float(leader_df["change_pct"].max()) if not leader_df.empty and "change_pct" in leader_df.columns else 0.0

    # 4) 事件（event_payload 可能是 list[dict] 或 dict）
    event_count = 0
    if isinstance(event_payload, list):
        event_count = len(event_payload)
    elif isinstance(event_payload, dict):
        event_count = int(event_payload.get("event_count", 0) or 0)

    # 5) 派生指标（保持与 SectorFeatureCalculator.build_sector_snapshot 一致口径）
    market_turnover = float(market_snapshot.get("market_turnover", 0.0))
    denominator = market_turnover if market_turnover > 0 else 1.0
    turnover_share = sector_turnover / denominator
    sync_move_ratio = rising_count / max(component_count, 1)
    breadth_score = float(min(max(sync_move_ratio, 0.0), 1.0))
    leader_strength_score = float(min(max((leader_change_pct + 15.0) / 30.0, 0.0), 1.0))
    continuation_score = float(min(max(0.6 * breadth_score + 0.4 * leader_strength_score, 0.0), 1.0))
    rotation_speed_score = 0.5  # 单板块无法算分位，给中位
    event_freshness_score = float(min(max(event_count / 5.0, 0.0), 1.0))
    risk_event_pressure_score = float(1.0 - event_freshness_score)
    state_fund_signal_score = float(market_snapshot.get("state_fund_signal_score", 0.0))

    # 6) sector_stage 推断（委托给 _infer_sector_stage）
    sector_stage = _infer_sector_stage(
        market_regime=market_regime,
        turnover_share=turnover_share,
        breadth_score=breadth_score,
        leader_strength_score=leader_strength_score,
    )

    snapshot: dict[str, Any] = {
        "sector_id": sector_id,
        "sector_name": sector_name,
        "sector_type": sector_type,
        "business_date": business_date,
        "feature_version": feature_version,
        "sector_turnover": sector_turnover,
        "sector_return": sector_return,
        "rising_count": rising_count,
        "component_count": component_count,
        "net_flow": net_flow,
        "main_net_flow": main_net_flow,
        "net_flow_acceleration": net_flow_acceleration,
        "leader_change_pct": leader_change_pct,
        "event_count": event_count,
        "turnover_share": turnover_share,
        "turnover_share_delta": 0.0,
        "turnover_share_trend": 0.0,
        "sync_move_ratio": sync_move_ratio,
        "breadth_score": breadth_score,
        "leader_strength_score": leader_strength_score,
        "continuation_score": continuation_score,
        "persistence_score": continuation_score,
        "rotation_speed_score": rotation_speed_score,
        "sector_stage": sector_stage,
        "mainline_flag": sector_stage in {SectorStage.CONFIRMED.value, SectorStage.STRENGTHENING.value},
        "event_freshness_score": event_freshness_score,
        "event_sector_mapping_score": event_freshness_score,
        "institution_bias_score": 0.5,
        "capital_rotation_out_score": 0.5,
        "risk_event_pressure_score": risk_event_pressure_score,
        "leader_ignition_score": leader_strength_score,
        "ladder_integrity_score": breadth_score,
        "profit_effect_spread_score": continuation_score,
        "high_low_coordination_score": 0.5 * breadth_score + 0.5 * leader_strength_score,
        "branch_rotation_health_score": rotation_speed_score,
        "sentiment_amplification_score": continuation_score,
        "sentiment_divergence_score": abs(leader_strength_score - breadth_score),
        "follow_through_quality_score": continuation_score,
        "consensus_score": 0.5 * breadth_score + 0.5 * event_freshness_score,
        "turnover_share_stability": 0.5,
        "turnover_share_fall_score": 0.0,
        "leader_decay_score": 1.0 - leader_strength_score,
        "breadth_contraction_score": 1.0 - breadth_score,
        "high_level_loss_effect_score": 1.0 - continuation_score,
        "market_regime_mismatch_score": 1.0 if market_regime == MarketRegime.RISK_OFF_PANIC.value else 0.2,
        "state_fund_signal_score": state_fund_signal_score,
        "stabilization_benefit_score": state_fund_signal_score,
        "theme_crowd_out_score": 0.5,
    }
    logger.info(
        "板块特征完成, sector=%s, stage=%s, turnover_share=%.4f, continuation=%.4f",
        sector_name,
        sector_stage,
        turnover_share,
        continuation_score,
    )
    return snapshot


def _infer_sector_stage(
    *,
    market_regime: str,
    turnover_share: float,
    breadth_score: float,
    leader_strength_score: float,
) -> str:
    """与 `SectorFeatureCalculator._infer_sector_stage` 阈值严格一致。"""
    if market_regime == MarketRegime.RISK_OFF_PANIC.value:
        return SectorStage.WATCHING.value
    if turnover_share >= 0.08 and breadth_score >= 0.55 and leader_strength_score >= 0.65:
        return SectorStage.STRENGTHENING.value
    if turnover_share >= 0.05 and breadth_score >= 0.4 and leader_strength_score >= 0.55:
        return SectorStage.CONFIRMED.value
    if turnover_share >= 0.03 and leader_strength_score >= 0.5:
        return SectorStage.EMERGING.value
    return SectorStage.WATCHING.value
