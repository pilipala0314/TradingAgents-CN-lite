"""回测内置策略单测。

覆盖：
- 4 个策略的 score 计算正确性
- 边界条件（end_idx 不足 / 数据缺失 / 0 close）
- registry / get_strategy / list_strategies 工具函数
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from services.strategies import (
    STRATEGY_REGISTRY,
    MeanReversionStrategy,
    MomentumStrategy,
    MultiFactorStrategy,
    ValueStrategy,
    get_strategy,
    list_strategies,
)


def _mk_bars(closes: list[float], start: date = date(2025, 1, 1)) -> list[dict]:
    """构造 bars list[dict] 工具函数。"""
    out: list[dict] = []
    for i, c in enumerate(closes):
        out.append(
            {
                "date": start + timedelta(days=i),
                "open": c,
                "high": c,
                "low": c,
                "close": c,
                "volume": 1_000_000,
            }
        )
    return out


# ── 动量策略 ─────────────────────────────────────────────

class TestMomentumStrategy:
    def test_basic_positive(self) -> None:
        s = MomentumStrategy(lookback=20)
        bars = _mk_bars([100.0] * 21)
        # 最后一日 vs 20 日前：都 100，收益 0
        assert s.score(bars, 20) == pytest.approx(0.0)

    def test_up_trend(self) -> None:
        s = MomentumStrategy(lookback=20)
        # 20 日前 100 → 今日 110，收益 10%
        bars = _mk_bars([100.0] * 20 + [110.0])
        assert s.score(bars, 20) == pytest.approx(0.10)

    def test_down_trend(self) -> None:
        s = MomentumStrategy(lookback=20)
        bars = _mk_bars([100.0] * 20 + [90.0])
        assert s.score(bars, 20) == pytest.approx(-0.10)

    def test_lookback_too_short(self) -> None:
        s = MomentumStrategy(lookback=20)
        bars = _mk_bars([100.0] * 5)
        assert s.score(bars, 5) == 0.0

    def test_zero_prev_close(self) -> None:
        s = MomentumStrategy(lookback=20)
        bars = _mk_bars([100.0] * 20 + [110.0])
        bars[0]["close"] = 0.0
        assert s.score(bars, 20) == 0.0

    def test_min_lookback(self) -> None:
        s = MomentumStrategy(lookback=20)
        assert s.min_lookback() == 21


# ── 反转策略 ─────────────────────────────────────────────

class TestMeanReversionStrategy:
    def test_basic(self) -> None:
        s = MeanReversionStrategy(lookback=5)
        # 5 日前 100 → 今日 110，反转分数 = -0.10
        bars = _mk_bars([100.0] * 5 + [110.0])
        assert s.score(bars, 5) == pytest.approx(-0.10)

    def test_down_then_up(self) -> None:
        # 5 日前 100 → 今日 90，反转分数 = +0.10（跌多优先买）
        s = MeanReversionStrategy(lookback=5)
        bars = _mk_bars([100.0] * 5 + [90.0])
        assert s.score(bars, 5) == pytest.approx(0.10)


# ── 价值策略 ─────────────────────────────────────────────

class TestValueStrategy:
    def test_below_ma(self) -> None:
        # 构造 250 根 bar，让 close < MA250 → 正分（优先买）
        # 用平稳序列 100 + 最后一根 80，MA ≈ 99.6，分数 ≈ -((80-99.6)/99.6) ≈ 0.197
        s = ValueStrategy(ma_window=250)
        closes = [100.0] * 250 + [80.0]
        bars = _mk_bars(closes)
        # end_idx = 250（最后一根），需 end_idx >= ma_window(250)
        score = s.score(bars, 250)
        assert score > 0.15 and score < 0.22

    def test_above_ma(self) -> None:
        s = ValueStrategy(ma_window=250)
        closes = [100.0] * 250 + [120.0]
        bars = _mk_bars(closes)
        score = s.score(bars, 250)
        assert score < -0.15 and score > -0.22

    def test_lookback_too_short(self) -> None:
        s = ValueStrategy(ma_window=250)
        bars = _mk_bars([100.0] * 10)
        # end_idx=9 < ma_window=250 → 0
        assert s.score(bars, 9) == 0.0


# ── 多因子策略 ───────────────────────────────────────────

class TestMultiFactorStrategy:
    def test_basic(self) -> None:
        s = MultiFactorStrategy(lookback_mom=60, lookback_vol=20)
        bars = _mk_bars([100.0] * 60 + [105.0])  # 上涨 +0.05
        # end_idx=60 >= lookback_mom(60)
        score = s.score(bars, 60)
        # 动量 +0.05 - 2*波动率(0.01 上下轻微) ≈ 0.02-0.05
        assert score > 0.02 and score < 0.06

    def test_high_vol_down(self) -> None:
        s = MultiFactorStrategy(lookback_mom=60, lookback_vol=20)
        # 构造高波动序列，最后一日跌
        closes = [100.0 + (i % 2) * 5 for i in range(60)] + [80.0]
        bars = _mk_bars(closes)
        score = s.score(bars, 60)
        # 动量 -0.20 + 高波动负分 → 总分明显为负
        assert score < 0.0


# ── registry / 工具函数 ───────────────────────────────────

class TestRegistry:
    def test_registry_has_4(self) -> None:
        assert len(STRATEGY_REGISTRY) == 4
        assert "momentum_20d" in STRATEGY_REGISTRY
        assert "mean_reversion_5d" in STRATEGY_REGISTRY
        assert "value_ma250" in STRATEGY_REGISTRY
        assert "multi_factor_mom_vol" in STRATEGY_REGISTRY

    def test_get_strategy(self) -> None:
        s = get_strategy("momentum_20d")
        assert isinstance(s, MomentumStrategy)
        assert s.lookback == 20

    def test_get_strategy_unknown(self) -> None:
        with pytest.raises(KeyError, match="未知策略"):
            get_strategy("nope")

    def test_list_strategies(self) -> None:
        items = list_strategies()
        assert len(items) == 4
        assert all("name" in i and "description" in i for i in items)
        names = {i["name"] for i in items}
        assert names == {
            "momentum_20d",
            "mean_reversion_5d",
            "value_ma250",
            "multi_factor_mom_vol",
        }
