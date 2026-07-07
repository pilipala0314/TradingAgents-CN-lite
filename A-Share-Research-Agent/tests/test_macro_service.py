"""macro_service 6 种 Market Regime 判定分支单测覆盖。

判定逻辑（services/macro_service.py:build_market_regime_from_macro L188-248）：
- 评分：pmi_recess (-2.0) + cpi_infl (-1.0) + m2_loose (+1.5)
        （bond_inverted 不参与 score，只作 risk_off_external_shock 配套触发器）
- 互斥分支：
    1. pmi_recess AND cpi_infl          → risk_off_stagflation
    2. pmi_recess AND NOT m2_loose      → risk_off_contraction
    3. pmi_recess AND bond_inverted     → risk_off_external_shock
    4. m2_loose AND NOT pmi_recess      → risk_on_expansion
    5. score > 0.5                       → risk_on_neutral
    6. score < -0.5                      → risk_off_contraction
    7. availability < 0.5                → transition_volatile
    8. NOT pmi_recess                    → risk_on_neutral
    9. else                              → transition_volatile
"""
from __future__ import annotations

import pytest

from services.macro_service import (
    MacroSnapshot,
    build_market_regime_from_macro,
)


def _make_snap(
    *,
    pmi: float | None = 50.0,
    cpi: float | None = 2.0,
    m2: float | None = 10.0,
    cn_10y: float | None = 2.5,
    us_10y: float | None = 4.0,
) -> MacroSnapshot:
    """便捷构造 MacroSnapshot（4 个端点 snapshot 都是 {value, as_of, source, available, raw}）。"""
    pmi_snap: dict = {"value": pmi, "as_of": "2026-06", "source": "pmi", "available": pmi is not None}
    cpi_snap: dict = {"value": cpi, "as_of": "2026-06", "source": "cpi", "available": cpi is not None}
    m2_snap: dict = {"value": m2, "as_of": "2026-06", "source": "m2", "available": m2 is not None}
    bond_snap: dict = {
        "value": None,
        "as_of": "2026-06",
        "source": "bond",
        "available": cn_10y is not None and us_10y is not None,
        "raw": {
            "中国国债收益率10年": cn_10y,
            "美国国债收益率10年": us_10y,
        },
    }
    return MacroSnapshot(
        pmi=pmi_snap,
        cpi=cpi_snap,
        m2=m2_snap,
        bond_yield=bond_snap,
    )


def _regime(snap: MacroSnapshot) -> str:
    """取 regime 字段。"""
    return build_market_regime_from_macro(snap)["regime"]


# ── 6 种 regime 分支 ─────────────────────────────────────────────────


class TestRiskOnExpansion:
    """分支 4: m2_loose (>11) AND NOT pmi_recess (<49.5) → risk_on_expansion。"""

    def test_m2_loose_pmi_normal(self) -> None:
        snap = _make_snap(pmi=51.0, m2=12.0)
        assert _regime(snap) == "risk_on_expansion"

    def test_m2_loose_pmi_high(self) -> None:
        # PMI 53 + M2 12% → 强扩张
        snap = _make_snap(pmi=53.0, m2=12.0, cpi=1.0)
        assert _regime(snap) == "risk_on_expansion"


class TestRiskOnNeutral:
    """分支 5/8: 偏多但不到 expansion；或 NOT pmi_recess + 其他中性。"""

    def test_pmi_high_m2_normal(self) -> None:
        # PMI 51（不 recess）+ M2 9.5（<11 不 loose） + 利差不倒挂
        # pmi_recess=False, cpi_infl=False, m2_loose=False, bond_inverted=False → score=0
        # 走 NOT pmi_recess → risk_on_neutral
        snap = _make_snap(pmi=51.0, m2=9.5, cpi=2.0, cn_10y=3.0, us_10y=2.0)
        assert _regime(snap) == "risk_on_neutral"

    def test_pmi_normal_bond_inverted(self) -> None:
        """PMI 正常 + bond 倒挂（无 PMI 衰退）→ 仍应 risk_on_neutral（修复后）"""
        snap = _make_snap(pmi=51.0, m2=9.5, cpi=2.0, cn_10y=1.5, us_10y=4.5)
        assert _regime(snap) == "risk_on_neutral"


class TestRiskOffContraction:
    """分支 2: pmi_recess AND NOT m2_loose → risk_off_contraction。"""

    def test_pmi_low_m2_normal(self) -> None:
        # PMI 49（recess） + M2 9.5（<11 不 loose）
        snap = _make_snap(pmi=49.0, m2=9.5, cpi=2.0)
        assert _regime(snap) == "risk_off_contraction"

    def test_pmi_low_m2_low(self) -> None:
        # PMI 48 + M2 8 + CPI 1.5 → 全部不利
        snap = _make_snap(pmi=48.0, m2=8.0, cpi=1.5)
        assert _regime(snap) == "risk_off_contraction"


class TestRiskOffStagflation:
    """分支 1: pmi_recess AND cpi_infl (>3) → risk_off_stagflation。"""

    def test_pmi_low_cpi_high(self) -> None:
        snap = _make_snap(pmi=49.0, cpi=3.5, m2=8.0)
        assert _regime(snap) == "risk_off_stagflation"

    def test_pmi_recess_cpi_just_above_3(self) -> None:
        snap = _make_snap(pmi=48.0, cpi=3.1, m2=7.0)
        assert _regime(snap) == "risk_off_stagflation"


class TestRiskOffExternalShock:
    """分支 3: pmi_recess AND bond_inverted (cn < us) → risk_off_external_shock。"""

    def test_pmi_recess_bond_inverted_no_stagflation(self) -> None:
        # PMI 49 + M2 12 (loose) + CPI 1.5 (不 infl) + cn 1.5 < us 4.5 (inverted)
        # stagflation 不满足（cpi 不 >3）
        # contraction 不满足（m2_loose）
        # external_shock 满足
        snap = _make_snap(pmi=49.0, m2=12.0, cpi=1.5, cn_10y=1.5, us_10y=4.5)
        assert _regime(snap) == "risk_off_external_shock"


class TestTransitionVolatile:
    """分支 7/9: 数据严重缺失（availability<0.5）或边界条件。"""

    def test_all_none(self) -> None:
        """所有数据 None → availability=0 → transition_volatile。"""
        snap = _make_snap(pmi=None, cpi=None, m2=None, cn_10y=None, us_10y=None)
        assert _regime(snap) == "transition_volatile"

    def test_half_data_missing(self) -> None:
        """只 2/4 有值 → availability=0.5 → 严格 < 0.5 不满足；但实际是 ≤，让 availability=0.5 不进 volatile。
        这里改成只 1/4 有值（availability=0.25 < 0.5）。"""
        snap = _make_snap(pmi=50.0, cpi=None, m2=None, cn_10y=None, us_10y=None)
        # availability = 1/4 = 0.25 < 0.5 → transition_volatile
        assert _regime(snap) == "transition_volatile"


# ── 边界 + 异常场景 ─────────────────────────────────────────────────


class TestEdgeCases:
    """边界值 / 极端输入。"""

    def test_pmi_exact_recess_threshold(self) -> None:
        """PMI 恰好 49.5（PMI_RECESS_THRESHOLD）→ _is_pmi_recession 用 < 判定 → False。"""
        # 利差非倒挂 (cn 3.0, us 2.0) → 避免已知设计缺陷：bond_inverted 单独导致 contraction
        snap = _make_snap(pmi=49.5, m2=8.0, cpi=2.0, cn_10y=3.0, us_10y=2.0)
        # PMI=49.5 not < 49.5, pmi_recess=False
        # score = 0 → 走 NOT pmi_recess → risk_on_neutral
        assert _regime(snap) == "risk_on_neutral"

    def test_pmi_just_below_recess(self) -> None:
        """PMI 49.4 → pmi_recess=True。"""
        # 利差非倒挂避免 bond_inverted 干扰
        snap = _make_snap(pmi=49.4, m2=8.0, cpi=2.0, cn_10y=3.0, us_10y=2.0)
        # pmi_recess=True, m2_loose=False → contraction
        assert _regime(snap) == "risk_off_contraction"

    def test_cpi_exact_inflation_threshold(self) -> None:
        """CPI=3.0（边界）→ 不 > 3，cpi_infl=False。"""
        # 利差非倒挂避免 bond_inverted 干扰
        snap = _make_snap(pmi=51.0, cpi=3.0, m2=8.0, cn_10y=3.0, us_10y=2.0)
        # pmi_recess=False, cpi_infl=False, m2_loose=False, bond_inverted=False
        # 走 NOT pmi_recess → risk_on_neutral
        assert _regime(snap) == "risk_on_neutral"

    def test_m2_exact_loose_threshold(self) -> None:
        """M2=11.0 边界 → > 11 才 loose，M2=11 不 loose。"""
        # 利差非倒挂避免 bond_inverted 干扰
        snap = _make_snap(pmi=51.0, m2=11.0, cpi=2.0, cn_10y=3.0, us_10y=2.0)
        # m2_loose=False (11.0 not > 11.0)
        # NOT pmi_recess → risk_on_neutral
        assert _regime(snap) == "risk_on_neutral"

    def test_m2_just_above_loose(self) -> None:
        """M2=11.1 → loose=True, pmi_recess=False → expansion。"""
        snap = _make_snap(pmi=51.0, m2=11.1, cpi=2.0)
        assert _regime(snap) == "risk_on_expansion"

    def test_negative_m2yoy(self) -> None:
        """M2 同比负增长（异常）→ 不抛错。"""
        # 利差非倒挂避免 bond_inverted 干扰
        snap = _make_snap(pmi=51.0, m2=-2.0, cpi=2.0, cn_10y=3.0, us_10y=2.0)
        # pmi_recess=False, m2_loose=False (负 < 11)
        # 走 NOT pmi_recess → risk_on_neutral
        assert _regime(snap) == "risk_on_neutral"

    def test_pmi_extreme_high(self) -> None:
        """PMI 60（极端高） + M2 normal。"""
        # 利差非倒挂避免 bond_inverted 干扰
        snap = _make_snap(pmi=60.0, m2=8.0, cpi=2.0, cn_10y=3.0, us_10y=2.0)
        # pmi_recess=False, m2_loose=False
        # NOT pmi_recess → risk_on_neutral
        assert _regime(snap) == "risk_on_neutral"

    def test_bond_not_inverted(self) -> None:
        """中美利差正常（cn > us）→ bond_inverted=False。"""
        # cn 3.0, us 2.0 → cn > us → not inverted
        snap = _make_snap(pmi=49.0, m2=8.0, cpi=2.0, cn_10y=3.0, us_10y=2.0)
        # pmi_recess=True, cpi_infl=False, m2_loose=False, bond_inverted=False
        # 走分支 2 (pmi_recess and not m2_loose) → contraction
        assert _regime(snap) == "risk_off_contraction"

    def test_bond_extreme_inverted(self) -> None:
        """极端倒挂（us >> cn） + PMI 收缩 + M2 宽松 + CPI 低。"""
        snap = _make_snap(pmi=49.0, m2=12.0, cpi=1.5, cn_10y=0.5, us_10y=5.5)
        # stagflation: NO (cpi=1.5)
        # contraction: NO (m2_loose=True)
        # external_shock: YES (pmi_recess + bond_inverted)
        assert _regime(snap) == "risk_off_external_shock"


# ── return 结构完整性 ───────────────────────────────────────────────


class TestReturnStructure:
    """校验返回 dict 的结构。"""

    def test_return_has_regime_triggers_confidence(self) -> None:
        snap = _make_snap(pmi=51.0, m2=8.0, cpi=2.0)
        result = build_market_regime_from_macro(snap)
        assert "regime" in result
        assert "triggers" in result
        assert "confidence" in result
        assert isinstance(result["regime"], str)
        assert isinstance(result["triggers"], list)
        assert 0.0 <= result["confidence"] <= 1.0

    def test_triggers_populated_when_signals_active(self) -> None:
        snap = _make_snap(pmi=49.0, m2=12.0, cpi=3.5, cn_10y=1.0, us_10y=5.0)
        result = build_market_regime_from_macro(snap)
        # pmi_recess + cpi_infl + m2_loose + bond_inverted 全部命中
        assert "pmi_below_50" in result["triggers"]
        assert "cpi_above_3pct" in result["triggers"]
        assert "m2_above_11pct" in result["triggers"]
        assert "cn_us_spread_inverted" in result["triggers"]


# ── 决策可追溯性：参数化 dispatch table ─────────────────────────────


@pytest.mark.parametrize(
    "snap,expected_regime",
    [
        # risk_on_expansion
        (_make_snap(pmi=51.0, m2=12.0), "risk_on_expansion"),
        (_make_snap(pmi=53.0, m2=12.0, cpi=1.0), "risk_on_expansion"),
        # risk_off_contraction
        (_make_snap(pmi=49.0, m2=8.0, cpi=1.5), "risk_off_contraction"),
        (_make_snap(pmi=48.0, m2=7.0, cpi=2.0), "risk_off_contraction"),
        # risk_off_stagflation
        (_make_snap(pmi=49.0, cpi=3.5, m2=8.0), "risk_off_stagflation"),
        (_make_snap(pmi=48.0, cpi=3.1, m2=7.0), "risk_off_stagflation"),
        # risk_off_external_shock
        (_make_snap(pmi=49.0, m2=12.0, cpi=1.5, cn_10y=1.5, us_10y=4.5), "risk_off_external_shock"),
        # risk_on_neutral（显式非倒挂，避免已知设计缺陷）
        (_make_snap(pmi=51.0, m2=9.5, cpi=2.0, cn_10y=3.0, us_10y=2.0), "risk_on_neutral"),
        # transition_volatile
        (_make_snap(pmi=None, cpi=None, m2=None, cn_10y=None, us_10y=None), "transition_volatile"),
    ],
)
def test_regime_dispatch_table(snap: MacroSnapshot, expected_regime: str) -> None:
    """参数化：每种 regime 至少 1 个独立 case 覆盖（外加边界用例 + 类内多 case）。"""
    assert _regime(snap) == expected_regime
