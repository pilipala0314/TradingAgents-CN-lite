"""StockAnalyst fallback 拆分函数单测。"""
from __future__ import annotations

from typing import Any

import pytest

from agents.stock_agents import (
    _apply_risk_adjustments,
    _derive_price_levels,
    _derive_tactical_flags,
    _derive_upstream_alignment,
    _dispatch_signal,
    _extract_features,
    _has_hard_block,
    _signal_for_a_kill_or_limit_flip,
    _signal_for_default_setup,
    _signal_for_diverging_sector,
    _signal_for_hard_block,
    _signal_for_moderate_setup,
    _signal_for_panic_market,
    _signal_for_strong_setup,
)
from core.enums import MarketRegime, SectorStage

# ── 纯函数单测 ────────────────────────────────────────


class TestExtractFeatures:
    def test_empty_snapshot_uses_defaults(self) -> None:
        f = _extract_features({})
        assert f["rs"] == 0.5
        assert f["crowd"] == 0.5
        assert f["famous_buy"] is False
        assert f["a_kill"] is False
        assert f["announcement_risk"] == 0.0
        assert f["valuation_percentile"] == 0.5
        assert f["support"] == 0.0
        assert f["resistance"] == 0.0
        assert f["stop_loss"] == 0.0

    def test_full_snapshot(self) -> None:
        snap = {
            "relative_strength_score": 0.8,
            "crowding_score": 0.4,
            "lhb_famous_buy_flag": True,
            "a_kill": True,
            "announcement_risk_score": 0.9,
            "valuation_percentile": 0.95,
            "support_level": 10.0,
            "resistance_level": 12.0,
            "stop_loss_hint": 9.5,
        }
        f = _extract_features(snap)
        assert f["rs"] == 0.8
        assert f["crowd"] == 0.4
        assert f["famous_buy"] is True
        assert f["a_kill"] is True
        assert f["announcement_risk"] == 0.9
        assert f["valuation_percentile"] == 0.95
        assert f["support"] == 10.0
        assert f["resistance"] == 12.0
        assert f["stop_loss"] == 9.5

    def test_invalid_string_falls_back_to_default(self) -> None:
        f = _extract_features({"relative_strength_score": "abc"})
        assert f["rs"] == 0.5
        f2 = _extract_features({"relative_strength_score": None})
        assert f2["rs"] == 0.5


class TestHasHardBlock:
    def test_empty(self) -> None:
        assert _has_hard_block([]) is False

    def test_warn_only(self) -> None:
        hits = [{"rule_level": "warn"}, {"rule_level": "info"}]
        assert _has_hard_block(hits) is False

    def test_block_present(self) -> None:
        hits = [{"rule_level": "warn"}, {"rule_level": "block"}]
        assert _has_hard_block(hits) is True


class TestSignalPresets:
    def test_hard_block(self) -> None:
        s = _signal_for_hard_block()
        assert s["trend_signal"] == "bearish"
        assert s["entry_timing"] == "avoid"
        assert s["risk_mode"] == "avoid"
        assert s["position_role"] == "laggard"
        assert s["conviction"] == 0.1

    def test_a_kill(self) -> None:
        s = _signal_for_a_kill_or_limit_flip()
        assert s["trend_signal"] == "bearish"
        assert s["conviction"] == 0.15

    def test_panic(self) -> None:
        s = _signal_for_panic_market()
        assert s["risk_mode"] == "avoid"
        assert s["conviction"] == 0.2

    def test_decaying(self) -> None:
        s = _signal_for_diverging_sector()
        assert s["trend_signal"] == "neutral"
        assert s["entry_timing"] == "wait_pullback"
        assert s["conviction"] == 0.45

    def test_strong_setup_expansion(self) -> None:
        s = _signal_for_strong_setup(
            market_regime=MarketRegime.RISK_ON_EXPANSION.value, rs=0.8
        )
        assert s["entry_timing"] == "now"
        assert s["risk_mode"] == "aggressive"
        assert s["conviction"] == pytest.approx(min(0.7 + 0.16, 0.9))

    def test_strong_setup_neutral(self) -> None:
        s = _signal_for_strong_setup(market_regime="neutral_mixed", rs=0.7)
        assert s["entry_timing"] == "wait_breakout"

    def test_moderate_setup_neutral_market(self) -> None:
        s = _signal_for_moderate_setup(MarketRegime.NEUTRAL_MIXED.value)
        assert s["trend_signal"] == "neutral"

    def test_moderate_setup_expansion(self) -> None:
        s = _signal_for_moderate_setup(MarketRegime.RISK_ON_EXPANSION.value)
        assert s["trend_signal"] == "bullish"

    def test_default(self) -> None:
        s = _signal_for_default_setup()
        assert s["trend_signal"] == "neutral"
        assert s["conviction"] == 0.4


class TestDispatchSignal:
    def test_a_kill_wins_over_panic(self) -> None:
        f = {"rs": 0.8, "crowd": 0.5, "famous_buy": True, "a_kill": True,
             "limit_up_to_down": False, "announcement_risk": 0.0, "valuation_percentile": 0.5,
             "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        s = _dispatch_signal(features=f, market_regime=MarketRegime.RISK_OFF_PANIC.value,
                             sector_stage=SectorStage.CONFIRMED.value)
        assert s["conviction"] == 0.15  # a_kill signal

    def test_panic_priority(self) -> None:
        f = {"rs": 0.8, "crowd": 0.5, "famous_buy": True, "a_kill": False,
             "limit_up_to_down": False, "announcement_risk": 0.0, "valuation_percentile": 0.5,
             "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        s = _dispatch_signal(features=f, market_regime=MarketRegime.RISK_OFF_PANIC.value,
                             sector_stage=SectorStage.CONFIRMED.value)
        assert s["conviction"] == 0.2  # panic signal

    def test_strong_setup_priority(self) -> None:
        f = {"rs": 0.7, "crowd": 0.5, "famous_buy": True, "a_kill": False,
             "limit_up_to_down": False, "announcement_risk": 0.0, "valuation_percentile": 0.5,
             "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        s = _dispatch_signal(features=f, market_regime="risk_on_expansion",
                             sector_stage=SectorStage.CONFIRMED.value)
        assert s["trend_signal"] == "bullish"
        assert s["entry_timing"] == "now"
        assert s["position_role"] == "leader"

    def test_strong_setup_blocked_by_crowding(self) -> None:
        f = {"rs": 0.7, "crowd": 0.8, "famous_buy": True, "a_kill": False,
             "limit_up_to_down": False, "announcement_risk": 0.0, "valuation_percentile": 0.5,
             "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        s = _dispatch_signal(features=f, market_regime="risk_on_expansion",
                             sector_stage=SectorStage.CONFIRMED.value)
        # crowd 0.8 >= 0.7，不走 strong setup → moderate 或 default
        assert s["position_role"] != "leader"

    def test_moderate_setup(self) -> None:
        f = {"rs": 0.6, "crowd": 0.5, "famous_buy": False, "a_kill": False,
             "limit_up_to_down": False, "announcement_risk": 0.0, "valuation_percentile": 0.5,
             "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        s = _dispatch_signal(features=f, market_regime="risk_on_expansion",
                             sector_stage=SectorStage.WATCHING.value)
        assert s["trend_signal"] == "bullish"
        assert s["conviction"] == 0.55

    def test_default_fallthrough(self) -> None:
        f = {"rs": 0.4, "crowd": 0.5, "famous_buy": False, "a_kill": False,
             "limit_up_to_down": False, "announcement_risk": 0.0, "valuation_percentile": 0.5,
             "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        s = _dispatch_signal(features=f, market_regime="neutral_mixed",
                             sector_stage=SectorStage.WATCHING.value)
        assert s["trend_signal"] == "neutral"
        assert s["conviction"] == 0.4


class TestApplyRiskAdjustments:
    def test_no_adjustment_below_threshold(self) -> None:
        sig: dict[str, Any] = {"trend_signal": "bullish", "entry_timing": "now",
                                "risk_mode": "aggressive", "position_role": "leader",
                                "conviction": 0.8}
        f = {"announcement_risk": 0.5, "valuation_percentile": 0.7, "rs": 0.7,
             "crowd": 0.5, "famous_buy": True, "a_kill": False,
             "limit_up_to_down": False, "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        out = _apply_risk_adjustments(sig.copy(), f)
        assert out["conviction"] == 0.8
        assert out["entry_timing"] == "now"

    def test_announcement_risk_penalty(self) -> None:
        sig: dict[str, Any] = {"trend_signal": "bullish", "entry_timing": "now",
                                "risk_mode": "aggressive", "position_role": "leader",
                                "conviction": 0.8}
        f = {"announcement_risk": 0.8, "valuation_percentile": 0.5, "rs": 0.7,
             "crowd": 0.5, "famous_buy": True, "a_kill": False,
             "limit_up_to_down": False, "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        out = _apply_risk_adjustments(sig.copy(), f)
        assert out["conviction"] == pytest.approx(0.6)
        assert out["entry_timing"] == "wait_pullback"

    def test_announcement_floor(self) -> None:
        sig: dict[str, Any] = {"trend_signal": "bearish", "entry_timing": "avoid",
                                "risk_mode": "avoid", "position_role": "laggard",
                                "conviction": 0.1}
        f = {"announcement_risk": 0.9, "valuation_percentile": 0.5, "rs": 0.5,
             "crowd": 0.5, "famous_buy": False, "a_kill": False,
             "limit_up_to_down": False, "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        out = _apply_risk_adjustments(sig.copy(), f)
        assert out["conviction"] == 0.1  # floor at 0.1

    def test_valuation_high_turns_defensive(self) -> None:
        sig: dict[str, Any] = {"trend_signal": "bullish", "entry_timing": "now",
                                "risk_mode": "aggressive", "position_role": "leader",
                                "conviction": 0.8}
        f = {"announcement_risk": 0.0, "valuation_percentile": 0.9, "rs": 0.7,
             "crowd": 0.5, "famous_buy": True, "a_kill": False,
             "limit_up_to_down": False, "support": 0.0, "resistance": 0.0, "stop_loss": 0.0}
        out = _apply_risk_adjustments(sig.copy(), f)
        assert out["entry_timing"] == "wait_pullback"
        assert out["risk_mode"] == "defensive"


class TestDeriveUpstreamAlignment:
    def test_panic_conflicting(self) -> None:
        assert _derive_upstream_alignment(
            trend_signal="bullish", market_regime=MarketRegime.RISK_OFF_PANIC.value,
            sector_stage=SectorStage.CONFIRMED.value, risk_mode="balanced"
        ) == "conflicting"

    def test_avoid_risk_mode_conflicting(self) -> None:
        assert _derive_upstream_alignment(
            trend_signal="bearish", market_regime="risk_on_expansion",
            sector_stage="emerging", risk_mode="avoid"
        ) == "conflicting"

    def test_bullish_in_watching_conflicting(self) -> None:
        assert _derive_upstream_alignment(
            trend_signal="bullish", market_regime="risk_on_expansion",
            sector_stage=SectorStage.WATCHING.value, risk_mode="balanced"
        ) == "conflicting"

    def test_neutral_trend(self) -> None:
        assert _derive_upstream_alignment(
            trend_signal="neutral", market_regime="risk_on_expansion",
            sector_stage="emerging", risk_mode="balanced"
        ) == "neutral"

    def test_aligned(self) -> None:
        assert _derive_upstream_alignment(
            trend_signal="bullish", market_regime="risk_on_expansion",
            sector_stage="confirmed", risk_mode="aggressive"
        ) == "aligned"


class TestDerivePriceLevels:
    def test_all_present(self) -> None:
        f = {"support": 10.0, "resistance": 12.0, "stop_loss": 9.5, "rs": 0.5,
             "crowd": 0.5, "famous_buy": False, "a_kill": False, "limit_up_to_down": False,
             "announcement_risk": 0.0, "valuation_percentile": 0.5}
        s, r, sl = _derive_price_levels(f)
        assert (s, r, sl) == (10.0, 12.0, 9.5)

    def test_support_missing_inferred_from_resistance(self) -> None:
        f = {"support": 0.0, "resistance": 100.0, "stop_loss": 0.0, "rs": 0.5,
             "crowd": 0.5, "famous_buy": False, "a_kill": False, "limit_up_to_down": False,
             "announcement_risk": 0.0, "valuation_percentile": 0.5}
        s, r, sl = _derive_price_levels(f)
        assert s == pytest.approx(92.0)
        assert r == 100.0
        assert sl == pytest.approx(89.24)  # 92 * 0.97

    def test_resistance_missing_inferred_from_support(self) -> None:
        f = {"support": 50.0, "resistance": 0.0, "stop_loss": 0.0, "rs": 0.5,
             "crowd": 0.5, "famous_buy": False, "a_kill": False, "limit_up_to_down": False,
             "announcement_risk": 0.0, "valuation_percentile": 0.5}
        s, r, sl = _derive_price_levels(f)
        assert s == 50.0
        assert r == pytest.approx(54.0)
        assert sl == pytest.approx(48.5)


class TestDeriveTacticalFlags:
    def test_no_flags(self) -> None:
        assert _derive_tactical_flags({}) == []

    def test_extracts_truthy_flags(self) -> None:
        snap = {"a_kill": True, "lhb_listed": True, "unknown_flag": True}
        flags = _derive_tactical_flags(snap)
        assert "a_kill" in flags
        assert "lhb_listed" in flags
        assert "unknown_flag" not in flags
