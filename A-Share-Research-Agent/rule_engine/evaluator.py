import logging
from typing import Any

from .gates.market_gates import MarketGates
from .gates.sector_gates import SectorGates
from .gates.stock_gates import StockGates

logger = logging.getLogger(__name__)

class RuleEngine:
    """
    统一规则执行引擎。
    在调用 LLM 之前，先过硬风控规则，输出结构化的 fact_rule_hit。
    """

    @staticmethod
    def evaluate_market(market_features: dict[str, Any]) -> list[dict[str, Any]]:
        """
        执行市场级评估
        """
        logger.info("开始执行市场级风控规则评估...")
        hits = MarketGates.evaluate_panic_melt_down(market_features)

        for hit in hits:
            logger.warning(f"触发市场级规则 [{hit['rule_code']}]: {hit['message']}")

        return hits

    @staticmethod
    def evaluate_sector(
        sector_features: dict[str, Any],
        market_regime: str,
    ) -> list[dict[str, Any]]:
        logger.info("开始执行板块级风控规则评估...")
        hits = SectorGates.evaluate_sector_rules(
            sector_features=sector_features,
            market_regime=market_regime,
        )

        for hit in hits:
            logger.warning(f"触发板块级规则 [{hit['rule_code']}]: {hit['message']}")

        return hits

    @staticmethod
    def evaluate_stock(stock_features: dict[str, Any]) -> list[dict[str, Any]]:
        """
        执行个股级评估
        """
        hits = StockGates.evaluate_hard_rules(stock_features)

        for hit in hits:
            logger.warning(f"触发个股级规则 [{hit['rule_code']}]: {hit['message']}")

        return hits
