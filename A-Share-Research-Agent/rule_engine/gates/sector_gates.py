from typing import Any


class SectorGates:
    """板块级准入与降级规则。"""

    @staticmethod
    def evaluate_sector_rules(sector_features: dict[str, Any], market_regime: str) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []

        if market_regime == "risk_off_panic" and sector_features.get("mainline_flag", False):
            hits.append(
                {
                    "rule_code": "SECTOR_MARKET_REGIME_BLOCK",
                    "rule_level": "constrain",
                    "hit_value": market_regime,
                    "action": "restrict",
                    "message": "市场处于 risk_off_panic，板块即使偏强也只允许观察，不允许积极进攻。",
                }
            )

        if float(sector_features.get("turnover_share", 0.0)) < 0.02 and float(
            sector_features.get("breadth_score", 0.0)
        ) < 0.2:
            hits.append(
                {
                    "rule_code": "SECTOR_LOW_ATTENTION",
                    "rule_level": "warning",
                    "hit_value": (
                        f"turnover_share={sector_features.get('turnover_share', 0.0):.4f},"
                        f"breadth_score={sector_features.get('breadth_score', 0.0):.4f}"
                    ),
                    "action": "warn",
                    "message": "板块关注度与扩散度均偏弱，不建议纳入主线池。",
                }
            )

        return hits
