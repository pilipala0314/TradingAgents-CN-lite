import logging
from typing import Any

logger = logging.getLogger(__name__)

class StockGates:
    """
    个股级风控规则门。
    """

    @staticmethod
    def evaluate_hard_rules(stock_features: dict[str, Any]) -> list[dict[str, Any]]:
        """
        判断是否触发个股硬性熔断（如 ST, 退市, 异常停牌, A杀等）
        """
        hits = []
        is_st = stock_features.get('is_st', False)
        abnormal_halt = stock_features.get('abnormal_halt_risk', False)
        a_kill = stock_features.get('a_kill', False)

        if is_st:
            hits.append({
                "rule_code": "STOCK_IS_ST",
                "rule_level": "block",
                "hit_value": "True",
                "action": "halt",
                "message": "标的为ST/*ST股，触发硬熔断，禁止交易推荐。"
            })

        if abnormal_halt:
            hits.append({
                "rule_code": "STOCK_ABNORMAL_HALT",
                "rule_level": "block",
                "hit_value": "True",
                "action": "halt",
                "message": "标的处于异常停牌风险状态，触发硬熔断。"
            })

        if a_kill:
            hits.append({
                "rule_code": "STOCK_A_KILL_PATTERN",
                "rule_level": "constrain",
                "hit_value": "True",
                "action": "reduce_position",
                "message": "标的出现A杀形态，触发风险约束，建议减仓或回避。"
            })

        return hits
