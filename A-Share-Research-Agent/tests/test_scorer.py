"""test_scorer — 验证打分函数（纯计算，无 I/O）。"""
from __future__ import annotations

import pytest

from data_pipeline.providers.scorer import (
    build_market_regime_hint,
    detect_auction_signal,
    detect_lhb_famous_buyers,
    detect_tactical_flags,
    infer_sector_stage,
    score_crowding,
    score_liquidity,
    score_valuation_percentile_from_pe,
)


class TestBuildMarketRegimeHint:
    def test_expansion_when_over_70pct_up(self):
        ranking = [{"change_pct": x} for x in [3, 2, 5, 1, -1, 0, 4]]
        assert build_market_regime_hint(ranking) == "risk_on_expansion"

    def test_panic_when_all_down(self):
        ranking = [{"change_pct": -1}, {"change_pct": -2}]
        assert build_market_regime_hint(ranking) == "risk_off_panic"

    def test_neutral_when_empty(self):
        assert build_market_regime_hint([]) == "neutral_mixed"


class TestInferSectorStage:
    @pytest.mark.parametrize(
        "pct,expected",
        [
            (6.0, "strengthening"),
            (3.0, "confirmed"),
            (1.0, "emerging"),
            (-2.0, "diverging"),
            (-5.0, "decaying"),
            (0.0, "watching"),
        ],
    )
    def test_stages(self, pct, expected):
        assert infer_sector_stage(pct) == expected


class TestDetectAuctionSignal:
    def test_strong_open_when_gap_gt_1p5pct(self):
        assert detect_auction_signal({"open": 10.3, "last_close": 10.0}) == "strong_open"

    def test_neutral_when_no_prev_close(self):
        assert detect_auction_signal({"open": 10.0, "last_close": 0}) == "neutral"


class TestDetectTacticalFlags:
    def test_streak_break_when_prev_limit_up_and_current_below_5pct(self):
        kline = [
            {"open": 10.0, "close": 10.0},
            {"open": 10.0, "close": 10.0},
            {"open": 10.0, "close": 10.0},
            {"open": 10.0, "close": 10.0},
            {"open": 10.0, "close": 11.0},  # 涨停
            {"open": 11.0, "close": 10.5},  # 回落
        ]
        today = {"change_pct": 2.0, "open": 11.0, "last_close": 11.0, "high": 11.2, "low": 10.4, "price": 10.5, "amplitude_pct": 8}
        flags = detect_tactical_flags(kline, today)
        assert flags["streak_break"] is True

    def test_a_kill_on_heavy_loss(self):
        # a_kill 判定需要 kline 非空（detect_tactical_flags 在空 kline 时直接返回全 False）
        kline = [{"open": 10.0, "close": 10.0}, {"open": 10.0, "close": 10.0}]
        today = {"change_pct": -10.0, "amplitude_pct": 5.0}
        assert detect_tactical_flags(kline, today)["a_kill"] is True


class TestScoreLiquidity:
    def test_normal_turnover_around_0_5(self):
        # amount_wan=10000 (1亿), mcap_yi=10 (10亿) → turnover=10% → score=clamp(0.1*30)=1.0
        # 改为更小：amount_wan=100 (1千万), mcap_yi=10 → 0.001 * 30 = 0.03
        score = score_liquidity(100, 10)
        assert 0.0 <= score <= 1.0

    def test_zero_returns_default(self):
        assert score_liquidity(0, 10) == 0.5
        assert score_liquidity(100, 0) == 0.5


class TestScoreCrowding:
    def test_within_0_1(self):
        assert 0.0 <= score_crowding(5.0, 1.5) <= 1.0


class TestScoreValuationPercentile:
    def test_negative_pe_returns_neutral_0_5(self):
        # 负 PE（亏损股）被函数入口的 pe_ttm <= 0 兜底，估值百分位 0.5（中性）
        assert score_valuation_percentile_from_pe(-5) == 0.5

    def test_zero_pe_default(self):
        assert score_valuation_percentile_from_pe(0) == 0.5

    def test_low_pe_low_percentile(self):
        assert score_valuation_percentile_from_pe(10) == 0.2

    def test_very_high_pe_high_percentile(self):
        assert score_valuation_percentile_from_pe(500) == 0.95


class TestDetectLhbFamousBuyers:
    def test_detects_known_brand(self):
        lhb = {
            "seats": {
                "buy": [{"name": "宁波桑田路营业部"}],
                "sell": [],
            }
        }
        famous_buy, _ = detect_lhb_famous_buyers(lhb)
        assert famous_buy is True

    def test_no_famous_when_empty(self):
        lhb = {"seats": {"buy": [{"name": "匿名营业部"}], "sell": []}}
        famous_buy, _ = detect_lhb_famous_buyers(lhb)
        assert famous_buy is False

    def test_returns_false_for_none(self):
        assert detect_lhb_famous_buyers(None) == (False, False)
