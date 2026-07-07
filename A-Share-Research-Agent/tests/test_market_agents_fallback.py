"""MarketRegimeAnalyst fallback 拆分函数单测。"""
from __future__ import annotations

from typing import Any

from agents.market_agents import _derive_risk_mode, _derive_style_hint
from core.enums import MarketRegime


class TestDeriveRiskMode:
    def test_block_forces_avoid(self) -> None:
        assert _derive_risk_mode(
            regime=MarketRegime.RISK_ON_EXPANSION.value,
            rule_hits=[{"rule_level": "block"}]
        ) == "avoid"

    def test_panic_regime_defensive(self) -> None:
        assert _derive_risk_mode(
            regime=MarketRegime.RISK_OFF_PANIC.value, rule_hits=[]
        ) == "defensive"

    def test_stabilized_regime_defensive(self) -> None:
        assert _derive_risk_mode(
            regime=MarketRegime.RISK_OFF_STABILIZED.value, rule_hits=[]
        ) == "defensive"

    def test_expansion_aggressive(self) -> None:
        assert _derive_risk_mode(
            regime=MarketRegime.RISK_ON_EXPANSION.value, rule_hits=[]
        ) == "aggressive"

    def test_rotation_aggressive(self) -> None:
        assert _derive_risk_mode(
            regime=MarketRegime.RISK_ON_ROTATION.value, rule_hits=[]
        ) == "aggressive"

    def test_neutral_balanced(self) -> None:
        assert _derive_risk_mode(
            regime=MarketRegime.NEUTRAL_MIXED.value, rule_hits=[]
        ) == "balanced"

    def test_warn_block_does_not_force_avoid(self) -> None:
        assert _derive_risk_mode(
            regime=MarketRegime.RISK_ON_EXPANSION.value,
            rule_hits=[{"rule_level": "warn"}]
        ) == "aggressive"


class TestDeriveStyleHint:
    def test_no_bias_default(self) -> None:
        assert _derive_style_hint({}) == "no_bias"

    def test_empty_string_no_bias(self) -> None:
        assert _derive_style_hint({"style_bias": ""}) == "no_bias"

    def test_known_values(self) -> None:
        for v in ("large_cap", "theme", "value", "growth"):
            assert _derive_style_hint({"style_bias": v}) == v

    def test_unknown_value_no_bias(self) -> None:
        assert _derive_style_hint({"style_bias": "small_cap"}) == "no_bias"
