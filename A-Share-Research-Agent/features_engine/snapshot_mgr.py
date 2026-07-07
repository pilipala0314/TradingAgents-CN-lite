import logging
from collections.abc import Mapping
from typing import Any

import pandas as pd

from core.config import settings
from features_engine.calculators.market_features import MarketFeatureCalculator
from features_engine.calculators.sector_features import SectorFeatureCalculator

logger = logging.getLogger(__name__)


class SnapshotManager:
    """统一的快照构建入口，负责把原始数据组装为 market / sector / stock 三类快照。"""

    @staticmethod
    def build_market_snapshot(
        business_date: Any,
        window_type: str,
        market_overview: Mapping[str, Any],
        limit_up_df: pd.DataFrame,
        limit_down_df: pd.DataFrame,
        bomb_board_df: pd.DataFrame,
    ) -> dict[str, Any]:
        features = MarketFeatureCalculator.calculate_limit_up_ecosystem(
            limit_up_df=limit_up_df,
            limit_down_df=limit_down_df,
            bomb_board_df=bomb_board_df,
        )
        features["business_date"] = business_date
        features["window_type"] = window_type
        features["total_turnover"] = float(market_overview.get("total_turnover", 0.0))
        features["advancers"] = int(market_overview.get("advancers", 0))
        features["decliners"] = int(market_overview.get("decliners", 0))
        features["northbound_net_inflow"] = float(
            market_overview.get("northbound_net_inflow", 0.0)
        )
        features["risk_preference_regime"] = MarketFeatureCalculator.infer_risk_preference_regime(
            limit_up_count=features["limit_up_count"],
            limit_down_count=features["limit_down_count"],
            bomb_rate=features["bomb_rate"],
        )
        features["risk_preference_score"] = MarketFeatureCalculator.compute_risk_preference_score(
            limit_up_count=features["limit_up_count"],
            limit_down_count=features["limit_down_count"],
            bomb_rate=features["bomb_rate"],
        )
        features["liquidity_regime"] = MarketFeatureCalculator.infer_liquidity_regime(
            total_turnover=features["total_turnover"]
        )
        features["liquidity_score"] = MarketFeatureCalculator.compute_liquidity_score(
            total_turnover=features["total_turnover"]
        )
        features["style_bias"] = str(market_overview.get("style_bias", "theme"))
        features["style_confidence"] = float(market_overview.get("style_confidence", 0.5))
        features["high_level_loss_effect_score"] = float(
            market_overview.get("high_level_loss_effect_score", features["bomb_rate"])
        )
        features["theme_survival_score"] = MarketFeatureCalculator.compute_theme_survival_score(
            risk_preference_score=features["risk_preference_score"],
            liquidity_score=features["liquidity_score"],
            high_level_loss_effect_score=features["high_level_loss_effect_score"],
        )
        features["market_regime"] = MarketFeatureCalculator.evaluate_market_regime(features)
        features["state_support_mode"] = str(market_overview.get("state_support_mode", "none"))
        features["state_support_proxy_score"] = float(
            market_overview.get("state_support_proxy_score", 0.0)
        )
        features["broad_etf_support_score"] = float(
            market_overview.get("broad_etf_support_score", 0.0)
        )
        features["large_cap_stabilization_score"] = float(
            market_overview.get("large_cap_stabilization_score", 0.0)
        )
        features["bank_shield_strength_score"] = float(
            market_overview.get("bank_shield_strength_score", 0.0)
        )
        features["dividend_defense_score"] = float(
            market_overview.get("dividend_defense_score", 0.0)
        )
        features["index_stock_divergence_score"] = float(
            market_overview.get("index_stock_divergence_score", 0.0)
        )
        features["tail_lift_intervention_score"] = float(
            market_overview.get("tail_lift_intervention_score", 0.0)
        )
        features["theme_suppression_score"] = float(
            market_overview.get("theme_suppression_score", 0.0)
        )
        features["market_sentiment_cycle"] = str(
            market_overview.get("market_sentiment_cycle", "neutral")
        )
        features["limit_up_streak_distribution"] = (
            MarketFeatureCalculator.compute_limit_up_streak_distribution(limit_up_df)
        )
        features["prev_zt_next_day_return"] = float(
            market_overview.get("prev_zt_next_day_return", 0.0)
        )
        features["feature_version"] = settings.DEFAULT_FEATURE_VERSION

        logger.info(
            "完成市场快照构建，business_date=%s window_type=%s regime=%s",
            business_date,
            window_type,
            features["market_regime"],
        )
        return features

    @staticmethod
    def build_sector_snapshot(
        sector_df: pd.DataFrame,
        market_snapshot: Mapping[str, Any],
        business_date: Any,
        window_type: str,
    ) -> pd.DataFrame:
        return SectorFeatureCalculator.build_sector_snapshot(
            sector_df=sector_df,
            market_turnover=float(market_snapshot.get("total_turnover", 0.0)),
            market_regime=str(market_snapshot.get("market_regime", "neutral_mixed")),
            business_date=business_date,
            window_type=window_type,
            feature_version=settings.DEFAULT_FEATURE_VERSION,
        )

    @staticmethod
    def build_stock_snapshot(
        stock_df: pd.DataFrame,
        business_date: Any,
        window_type: str,
        prev_limit_up_list: set | None = None,
        current_limit_up_list: set | None = None,
        current_limit_down_list: set | None = None,
    ) -> pd.DataFrame:
        from features_engine.calculators.stock_tactical import StockTacticalCalculator

        tactical_df = StockTacticalCalculator.calculate_tactical_labels(
            current_daily_df=stock_df,
            prev_limit_up_list=prev_limit_up_list or set(),
            current_limit_up_list=current_limit_up_list or set(),
            current_limit_down_list=current_limit_down_list or set(),
        )
        tactical_df["business_date"] = business_date
        tactical_df["window_type"] = window_type
        tactical_df["feature_version"] = settings.DEFAULT_FEATURE_VERSION
        return tactical_df
