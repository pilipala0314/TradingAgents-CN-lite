import pandas as pd

from features_engine.calculators.market_features import MarketFeatureCalculator


def test_calculate_limit_up_ecosystem_basic_metrics():
    limit_up_df = pd.DataFrame(
        {
            "连板数": [2, 4, 1],
            "首次封板时间": ["09:25:00", "10:11:00", "09:25:00"],
        }
    )
    limit_down_df = pd.DataFrame({"代码": ["000001"]})
    bomb_board_df = pd.DataFrame({"代码": ["000002", "000003"]})

    features = MarketFeatureCalculator.calculate_limit_up_ecosystem(
        limit_up_df=limit_up_df,
        limit_down_df=limit_down_df,
        bomb_board_df=bomb_board_df,
    )

    assert features["limit_up_count"] == 3
    assert features["limit_down_count"] == 1
    assert round(features["bomb_rate"], 4) == 0.4
    assert features["max_board_height"] == 4
    assert round(features["one_board_ratio"], 4) == round(2 / 3, 4)


def test_evaluate_market_regime_risk_off_panic():
    regime = MarketFeatureCalculator.evaluate_market_regime(
        {
            "bomb_rate": 0.45,
            "limit_down_count": 66,
            "limit_up_count": 22,
            "theme_survival_score": 0.1,
            "liquidity_regime": "tight",
            "risk_preference_regime": "risk_off",
        }
    )
    assert regime == "risk_off_panic"
