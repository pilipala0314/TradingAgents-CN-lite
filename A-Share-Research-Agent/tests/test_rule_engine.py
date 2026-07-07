from rule_engine.evaluator import RuleEngine


def test_market_rule_engine_triggers_block_when_bomb_rate_too_high():
    hits = RuleEngine.evaluate_market({"bomb_rate": 0.5, "limit_down_count": 10})
    assert any(hit["rule_code"] == "MARKET_HIGH_BOMB_RATE" for hit in hits)


def test_sector_rule_engine_warns_low_attention_sector():
    hits = RuleEngine.evaluate_sector(
        sector_features={
            "mainline_flag": False,
            "turnover_share": 0.01,
            "breadth_score": 0.1,
        },
        market_regime="neutral_mixed",
    )
    assert any(hit["rule_code"] == "SECTOR_LOW_ATTENTION" for hit in hits)


def test_stock_rule_engine_blocks_st_stock():
    hits = RuleEngine.evaluate_stock({"is_st": True, "abnormal_halt_risk": False})
    assert any(hit["rule_code"] == "STOCK_IS_ST" for hit in hits)
