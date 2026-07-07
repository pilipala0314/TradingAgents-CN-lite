"""SectorAnalyst fallback 拆分函数单测。"""
from __future__ import annotations

from typing import Any

import pytest

from agents.sector_agents import (
    _apply_market_regime_constraint,
    _apply_rule_blocks,
    _compute_sector_score,
    _score_to_stage,
)
from core.enums import MarketRegime, SectorStage


class TestComputeSectorScore:
    def test_empty(self) -> None:
        assert _compute_sector_score({}) == 0.0

    def test_three_full(self) -> None:
        snap: dict[str, Any] = {
            "continuation_score": 0.9,
            "flow_inflow_score": 0.8,
            "event_confirmation_score": 0.7,
        }
        assert _compute_sector_score(snap) == pytest.approx(0.8)

    def test_partial(self) -> None:
        snap: dict[str, Any] = {"continuation_score": 0.6}
        assert _compute_sector_score(snap) == pytest.approx(0.2)

    def test_invalid_values_skipped(self) -> None:
        snap: dict[str, Any] = {
            "continuation_score": "abc",
            "flow_inflow_score": None,
            "event_confirmation_score": 0.6,
        }
        assert _compute_sector_score(snap) == pytest.approx(0.2)

    def test_clamp_to_unit(self) -> None:
        snap: dict[str, Any] = {
            "continuation_score": 2.0,
            "flow_inflow_score": 2.0,
            "event_confirmation_score": 2.0,
        }
        assert _compute_sector_score(snap) == 1.0

    def test_floor_at_zero(self) -> None:
        snap: dict[str, Any] = {
            "continuation_score": -0.5,
            "flow_inflow_score": -0.5,
            "event_confirmation_score": -0.5,
        }
        assert _compute_sector_score(snap) == 0.0


class TestScoreToStage:
    def test_strengthening(self) -> None:
        assert _score_to_stage(0.85) == SectorStage.STRENGTHENING.value

    def test_confirmed(self) -> None:
        assert _score_to_stage(0.65) == SectorStage.CONFIRMED.value

    def test_emerging(self) -> None:
        assert _score_to_stage(0.45) == SectorStage.EMERGING.value

    def test_watching(self) -> None:
        assert _score_to_stage(0.2) == SectorStage.WATCHING.value

    def test_diverging(self) -> None:
        assert _score_to_stage(0.05) == SectorStage.DIVERGING.value

    def test_boundary_075(self) -> None:
        assert _score_to_stage(0.75) == SectorStage.STRENGTHENING.value

    def test_boundary_010(self) -> None:
        assert _score_to_stage(0.1) == SectorStage.DIVERGING.value


class TestApplyMarketRegimeConstraint:
    def test_risk_off_panic_emerging_downgraded(self) -> None:
        stage, score, mainline = _apply_market_regime_constraint(
            stage=SectorStage.EMERGING.value, score=0.5,
            market_regime=MarketRegime.RISK_OFF_PANIC.value
        )
        assert stage == SectorStage.WATCHING.value
        assert score == 0.3
        assert mainline is False

    def test_risk_off_panic_confirmed_unchanged_score(self) -> None:
        stage, score, mainline = _apply_market_regime_constraint(
            stage=SectorStage.CONFIRMED.value, score=0.7,
            market_regime=MarketRegime.RISK_OFF_PANIC.value
        )
        assert stage == SectorStage.CONFIRMED.value
        assert score == 0.7
        assert mainline is False

    def test_normal_market_high_score_mainline(self) -> None:
        stage, score, mainline = _apply_market_regime_constraint(
            stage=SectorStage.CONFIRMED.value, score=0.7,
            market_regime=MarketRegime.RISK_ON_EXPANSION.value
        )
        assert mainline is True

    def test_normal_market_low_score_no_mainline(self) -> None:
        stage, score, mainline = _apply_market_regime_constraint(
            stage=SectorStage.EMERGING.value, score=0.4,
            market_regime=MarketRegime.RISK_ON_EXPANSION.value
        )
        assert mainline is False


class TestApplyRuleBlocks:
    def test_no_block(self) -> None:
        stage, mainline = _apply_rule_blocks(
            stage=SectorStage.CONFIRMED.value, mainline_flag=True, rule_hits=[]
        )
        assert stage == SectorStage.CONFIRMED.value
        assert mainline is True

    def test_warn_block_ignored(self) -> None:
        hits = [{"rule_level": "warn"}]
        stage, mainline = _apply_rule_blocks(
            stage=SectorStage.CONFIRMED.value, mainline_flag=True, rule_hits=hits
        )
        assert stage == SectorStage.CONFIRMED.value
        assert mainline is True

    def test_hard_block_forces_diverging(self) -> None:
        hits = [{"rule_level": "block", "rule_id": "abnormal_halt"}]
        stage, mainline = _apply_rule_blocks(
            stage=SectorStage.STRENGTHENING.value, mainline_flag=True, rule_hits=hits
        )
        assert stage == SectorStage.DIVERGING.value
        assert mainline is False
