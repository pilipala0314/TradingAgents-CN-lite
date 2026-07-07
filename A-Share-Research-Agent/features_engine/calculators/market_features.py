import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class MarketFeatureCalculator:
    """市场特征与生态计算器。"""

    @staticmethod
    def calculate_limit_up_ecosystem(
        limit_up_df: pd.DataFrame,
        limit_down_df: pd.DataFrame,
        bomb_board_df: pd.DataFrame
    ) -> dict[str, Any]:
        """基于涨停、跌停、炸板池计算市场打板生态的核心快照特征。"""
        features = {}

        limit_up_count = len(limit_up_df) if not limit_up_df.empty else 0
        limit_down_count = len(limit_down_df) if not limit_down_df.empty else 0
        bomb_count = len(bomb_board_df) if not bomb_board_df.empty else 0

        features["limit_up_count"] = limit_up_count
        features["limit_down_count"] = limit_down_count

        total_touch_limit = limit_up_count + bomb_count
        features["bomb_rate"] = bomb_count / total_touch_limit if total_touch_limit > 0 else 0.0

        if not limit_up_df.empty and "连板数" in limit_up_df.columns:
            try:
                max_board = pd.to_numeric(limit_up_df["连板数"], errors="coerce").fillna(0).max()
                features["max_board_height"] = int(max_board)
            except Exception:
                features["max_board_height"] = 0
        else:
            features["max_board_height"] = 0

        features["reseal_rate"] = 0.0

        if not limit_up_df.empty and "首次封板时间" in limit_up_df.columns:
            normalized_limit_up_df = limit_up_df.copy()
            normalized_limit_up_df["首次封板时间"] = normalized_limit_up_df["首次封板时间"].astype(str)
            one_board_mask = normalized_limit_up_df["首次封板时间"].str.startswith("09:25")
            one_board_count = one_board_mask.sum()

            features["one_board_ratio"] = (
                one_board_count / limit_up_count if limit_up_count > 0 else 0.0
            )
            features["turnover_board_ratio"] = (
                (limit_up_count - one_board_count) / limit_up_count if limit_up_count > 0 else 0.0
            )
        else:
            features["one_board_ratio"] = 0.0
            features["turnover_board_ratio"] = 0.0

        logger.info(
            "市场生态特征计算完成: 涨停=%s 跌停=%s 炸板率=%.2f%%",
            limit_up_count,
            limit_down_count,
            features["bomb_rate"] * 100,
        )
        return features

    @staticmethod
    def compute_limit_up_streak_distribution(limit_up_df: pd.DataFrame) -> dict[str, int]:
        if limit_up_df.empty or "连板数" not in limit_up_df.columns:
            return {}

        streaks = pd.to_numeric(limit_up_df["连板数"], errors="coerce").fillna(0).astype(int)
        distribution = {
            "2板": int((streaks == 2).sum()),
            "3板": int((streaks == 3).sum()),
            "4板+": int((streaks >= 4).sum()),
        }
        return distribution

    @staticmethod
    def infer_risk_preference_regime(limit_up_count: int, limit_down_count: int, bomb_rate: float) -> str:
        if limit_down_count >= 40 or bomb_rate >= 0.35:
            return "risk_off"
        if limit_up_count >= 60 and bomb_rate <= 0.18:
            return "risk_on"
        return "neutral"

    @staticmethod
    def compute_risk_preference_score(
        limit_up_count: int,
        limit_down_count: int,
        bomb_rate: float,
    ) -> float:
        raw_score = (limit_up_count * 1.2 - limit_down_count * 1.5) / 100.0 - bomb_rate
        return max(0.0, min(1.0, 0.5 + raw_score))

    @staticmethod
    def infer_liquidity_regime(total_turnover: float) -> str:
        if total_turnover >= 1.5e12:
            return "ample"
        if total_turnover >= 8.0e11:
            return "normal"
        return "tight"

    @staticmethod
    def compute_liquidity_score(total_turnover: float) -> float:
        return max(0.0, min(1.0, total_turnover / 1.5e12))

    @staticmethod
    def compute_theme_survival_score(
        risk_preference_score: float,
        liquidity_score: float,
        high_level_loss_effect_score: float,
    ) -> float:
        score = 0.45 * risk_preference_score + 0.35 * liquidity_score + 0.20 * (
            1.0 - high_level_loss_effect_score
        )
        return max(0.0, min(1.0, score))

    @staticmethod
    def evaluate_market_regime(features: dict[str, Any]) -> str:
        bomb_rate = float(features.get("bomb_rate", 0.0))
        limit_down_count = int(features.get("limit_down_count", 0))
        limit_up_count = int(features.get("limit_up_count", 0))
        theme_survival_score = float(features.get("theme_survival_score", 0.0))
        liquidity_regime = str(features.get("liquidity_regime", "normal"))
        risk_preference_regime = str(features.get("risk_preference_regime", "neutral"))
        state_support_proxy_score = float(features.get("state_support_proxy_score", 0.0))
        index_stock_divergence_score = float(features.get("index_stock_divergence_score", 0.0))

        if risk_preference_regime == "risk_off" and (
            limit_down_count > 50 or bomb_rate > 0.4 or theme_survival_score < 0.25
        ):
            return "risk_off_panic"
        if (
            risk_preference_regime == "risk_off"
            and state_support_proxy_score > 0.5
            and index_stock_divergence_score > 0.4
        ):
            return "risk_off_stabilized"
        if risk_preference_regime == "risk_on" and liquidity_regime != "tight" and bomb_rate < 0.2:
            return "risk_on_expansion"
        if risk_preference_regime in {"risk_on", "neutral"} and limit_up_count > 40:
            return "risk_on_rotation"
        return "neutral_mixed"
