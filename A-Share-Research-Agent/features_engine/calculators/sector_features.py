import logging
from typing import Any

import pandas as pd

from core.enums import MarketRegime, SectorStage

logger = logging.getLogger(__name__)


class SectorFeatureCalculator:
    """板块层特征计算器，优先生成规则层与工作流可直接消费的结构化快照。"""

    @staticmethod
    def build_sector_snapshot(
        sector_df: pd.DataFrame,
        market_turnover: float,
        market_regime: str,
        business_date: Any,
        window_type: str,
        feature_version: int = 1,
    ) -> pd.DataFrame:
        if sector_df.empty:
            return pd.DataFrame()

        working_df = sector_df.copy()
        working_df["sector_turnover"] = pd.to_numeric(
            working_df.get("sector_turnover", 0.0), errors="coerce"
        ).fillna(0.0)
        working_df["sector_return"] = pd.to_numeric(
            working_df.get("sector_return", 0.0), errors="coerce"
        ).fillna(0.0)
        working_df["rising_count"] = pd.to_numeric(
            working_df.get("rising_count", 0), errors="coerce"
        ).fillna(0)
        working_df["component_count"] = pd.to_numeric(
            working_df.get("component_count", 0), errors="coerce"
        ).fillna(0)

        denominator = market_turnover if market_turnover > 0 else 1.0
        working_df["turnover_share"] = working_df["sector_turnover"] / denominator
        working_df["sync_move_ratio"] = (
            working_df["rising_count"] / working_df["component_count"].clip(lower=1)
        )
        working_df["breadth_score"] = working_df["sync_move_ratio"].clip(lower=0.0, upper=1.0)
        working_df["leader_strength_score"] = (
            pd.to_numeric(working_df.get("leader_change_pct", 0.0), errors="coerce")
            .fillna(0.0)
            .clip(lower=-15.0, upper=15.0)
            .add(15.0)
            .div(30.0)
        )
        working_df["continuation_score"] = (
            0.6 * working_df["breadth_score"] + 0.4 * working_df["leader_strength_score"]
        ).clip(lower=0.0, upper=1.0)
        working_df["persistence_score"] = working_df["continuation_score"]
        working_df["rotation_speed_score"] = (
            1.0 - working_df["turnover_share"].rank(pct=True)
        ).clip(lower=0.0, upper=1.0)

        working_df["sector_stage"] = working_df.apply(
            lambda row: SectorFeatureCalculator._infer_sector_stage(
                market_regime=market_regime,
                turnover_share=float(row["turnover_share"]),
                breadth_score=float(row["breadth_score"]),
                leader_strength_score=float(row["leader_strength_score"]),
            ),
            axis=1,
        )
        working_df["mainline_flag"] = working_df["sector_stage"].isin(
            [SectorStage.CONFIRMED.value, SectorStage.STRENGTHENING.value]
        )
        working_df["business_date"] = business_date
        working_df["window_type"] = window_type
        working_df["feature_version"] = feature_version
        if "turnover_share_delta" in working_df.columns:
            working_df["turnover_share_delta"] = pd.to_numeric(
                working_df["turnover_share_delta"], errors="coerce"
            ).fillna(0.0)
        else:
            working_df["turnover_share_delta"] = 0.0

        if "turnover_share_trend" in working_df.columns:
            working_df["turnover_share_trend"] = pd.to_numeric(
                working_df["turnover_share_trend"], errors="coerce"
            ).fillna(0.0)
        else:
            working_df["turnover_share_trend"] = working_df["turnover_share_delta"]

        if "net_flow_acceleration" in working_df.columns:
            working_df["net_flow_acceleration"] = pd.to_numeric(
                working_df["net_flow_acceleration"], errors="coerce"
            ).fillna(0.0)
        else:
            working_df["net_flow_acceleration"] = 0.0
        if "event_count" in working_df.columns:
            working_df["event_count"] = pd.to_numeric(
                working_df["event_count"], errors="coerce"
            ).fillna(0)
        else:
            working_df["event_count"] = 0
        working_df["event_freshness_score"] = (
            working_df["event_count"].clip(lower=0, upper=5).div(5.0)
        )
        working_df["event_sector_mapping_score"] = working_df["event_freshness_score"]
        working_df["institution_bias_score"] = (
            working_df["net_flow_acceleration"].rank(pct=True) if len(working_df) > 1 else 1.0
        )
        working_df["capital_rotation_out_score"] = (
            1.0 - (working_df["net_flow_acceleration"].rank(pct=True) if len(working_df) > 1 else 1.0)
        )
        working_df["risk_event_pressure_score"] = (
            1.0 - working_df["event_freshness_score"]
        ).clip(lower=0.0, upper=1.0)
        working_df["leader_ignition_score"] = working_df["leader_strength_score"]
        working_df["ladder_integrity_score"] = working_df["breadth_score"]
        working_df["profit_effect_spread_score"] = working_df["continuation_score"]
        working_df["high_low_coordination_score"] = (
            0.5 * working_df["breadth_score"] + 0.5 * working_df["leader_strength_score"]
        ).clip(lower=0.0, upper=1.0)
        working_df["branch_rotation_health_score"] = working_df["rotation_speed_score"]
        working_df["sentiment_amplification_score"] = working_df["continuation_score"]
        working_df["sentiment_divergence_score"] = (
            (working_df["leader_strength_score"] - working_df["breadth_score"]).abs()
        ).clip(lower=0.0, upper=1.0)
        working_df["follow_through_quality_score"] = working_df["persistence_score"]
        working_df["consensus_score"] = (
            0.5 * working_df["breadth_score"] + 0.5 * working_df["event_sector_mapping_score"]
        ).clip(lower=0.0, upper=1.0)
        working_df["turnover_share_stability"] = (
            1.0 - working_df["turnover_share_delta"].abs().clip(upper=1.0)
        ).clip(lower=0.0, upper=1.0)
        working_df["turnover_share_fall_score"] = (
            (-working_df["turnover_share_delta"]).clip(lower=0.0, upper=1.0)
        )
        working_df["leader_decay_score"] = (
            1.0 - working_df["leader_strength_score"]
        ).clip(lower=0.0, upper=1.0)
        working_df["breadth_contraction_score"] = (
            1.0 - working_df["breadth_score"]
        ).clip(lower=0.0, upper=1.0)
        working_df["high_level_loss_effect_score"] = (
            1.0 - working_df["continuation_score"]
        ).clip(lower=0.0, upper=1.0)
        working_df["market_regime_mismatch_score"] = 1.0 if market_regime == MarketRegime.RISK_OFF_PANIC.value else 0.2
        if "state_fund_signal_score" in working_df.columns:
            working_df["state_fund_signal_score"] = pd.to_numeric(
                working_df["state_fund_signal_score"], errors="coerce"
            ).fillna(0.0)
        else:
            working_df["state_fund_signal_score"] = 0.0
        working_df["stabilization_benefit_score"] = working_df["state_fund_signal_score"]
        working_df["theme_crowd_out_score"] = (
            working_df["turnover_share"].rank(pct=True) if len(working_df) > 1 else 0.5
        )

        logger.info("完成板块快照构建，共 %s 个板块。", len(working_df))
        return working_df

    @staticmethod
    def _infer_sector_stage(
        market_regime: str,
        turnover_share: float,
        breadth_score: float,
        leader_strength_score: float,
    ) -> str:
        if market_regime == MarketRegime.RISK_OFF_PANIC.value:
            return SectorStage.WATCHING.value

        if turnover_share >= 0.08 and breadth_score >= 0.55 and leader_strength_score >= 0.65:
            return SectorStage.STRENGTHENING.value
        if turnover_share >= 0.05 and breadth_score >= 0.4 and leader_strength_score >= 0.55:
            return SectorStage.CONFIRMED.value
        if turnover_share >= 0.03 and leader_strength_score >= 0.5:
            return SectorStage.EMERGING.value
        return SectorStage.WATCHING.value
