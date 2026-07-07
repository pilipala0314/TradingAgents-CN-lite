import logging
from typing import Any

logger = logging.getLogger(__name__)

class MarketGates:
    """
    市场级风控规则门。
    对应文档 [8.2 风控分层] 与市场极寒状态熔断。
    """

    @staticmethod
    def evaluate_panic_melt_down(market_features: dict[str, Any]) -> list[dict[str, Any]]:
        """
        判断是否触发全市场熔断 (risk_off_panic)
        """
        hits = []
        bomb_rate = market_features.get('bomb_rate', 0.0)
        limit_down_count = market_features.get('limit_down_count', 0)

        # 规则1: 炸板率过高
        if bomb_rate > 0.4:
            hits.append({
                "rule_code": "MARKET_HIGH_BOMB_RATE",
                "rule_level": "block",
                "hit_value": f"bomb_rate={bomb_rate:.2%}",
                "action": "halt",
                "message": "全市场炸板率超过40%，追高风险极大，触发市场级防守熔断。"
            })

        # 规则2: 大面积跌停
        if limit_down_count > 100:
            hits.append({
                "rule_code": "MARKET_MASSIVE_LIMIT_DOWN",
                "rule_level": "block",
                "hit_value": f"limit_down_count={limit_down_count}",
                "action": "halt",
                "message": "全市场跌停家数过百，恐慌蔓延，触发市场级防守熔断。"
            })

        return hits
