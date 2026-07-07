"""回测指标单测。

覆盖：
- 11 个单点指标 + compute_metrics 综合
- nav_curve / returns_from_nav 互转
- 边界条件（空 / 全 0 / 单点 / 极端值）
"""
from __future__ import annotations

import math

import pytest

from services.backtest_metrics import (
    annualized_return,
    annualized_volatility,
    calmar_ratio,
    compute_metrics,
    max_drawdown,
    nav_curve,
    profit_loss_ratio,
    returns_from_nav,
    sharpe_ratio,
    total_return,
    win_rate,
)

# ── 总收益 / 年化 ─────────────────────────────────────────

class TestTotalReturn:
    def test_empty(self) -> None:
        assert total_return([]) == 0.0

    def test_basic(self) -> None:
        # (1+0.1)*(1+(-0.05)) - 1 = 0.045
        assert total_return([0.1, -0.05]) == pytest.approx(0.045)

    def test_all_positive(self) -> None:
        # (1.01)^5 - 1
        expected = (1.01**5) - 1.0
        assert total_return([0.01] * 5) == pytest.approx(expected)


class TestAnnualizedReturn:
    def test_empty(self) -> None:
        assert annualized_return([]) == 0.0

    def test_daily_252(self) -> None:
        # 252 日每日 0.001 → 年化 ≈ 0.284
        r = [0.001] * 252
        ann = annualized_return(r, periods_per_year=252)
        assert ann == pytest.approx((1.001**252) - 1, rel=1e-6)

    def test_weekly_52(self) -> None:
        # 52 周每周 0.02 → 年化 ≈ 1.812
        r = [0.02] * 52
        ann = annualized_return(r, periods_per_year=52)
        assert ann == pytest.approx((1.02**52) - 1, rel=1e-6)


# ── 年化波动 / 夏普 ──────────────────────────────────────

class TestAnnualizedVolatility:
    def test_empty(self) -> None:
        assert annualized_volatility([]) == 0.0

    def test_one_point(self) -> None:
        assert annualized_volatility([0.01]) == 0.0

    def test_constant_returns(self) -> None:
        # 全相等 → std=0 → 波动 0（容差 1e-12）
        assert annualized_volatility([0.01] * 100) == pytest.approx(0.0, abs=1e-12)

    def test_basic(self) -> None:
        # [-0.01, 0.01] 方差 = 0.0001，std = 0.01
        vol = annualized_volatility([-0.01, 0.01])
        assert vol == pytest.approx(0.01 * math.sqrt(252))


class TestSharpe:
    def test_empty(self) -> None:
        assert sharpe_ratio([]) == 0.0

    def test_zero_vol(self) -> None:
        assert sharpe_ratio([0.01] * 100) == 0.0

    def test_basic(self) -> None:
        r = [0.001, -0.001] * 50  # mean 0，std ≈ 0.001，sharpe 取决于年化
        s = sharpe_ratio(r)
        # mean=0 → 年化收益 ≈ 0 → sharpe 趋近 0（容差 0.01）
        assert abs(s) < 0.01


# ── 最大回撤 / Calmar ────────────────────────────────────

class TestMaxDrawdown:
    def test_empty(self) -> None:
        assert max_drawdown([]) == 0.0

    def test_monotonic_up(self) -> None:
        # 一直涨 → 0 回撤
        assert max_drawdown([0.01] * 10) == 0.0

    def test_basic(self) -> None:
        # 涨 50% → 跌 40% → 净值 1.5 → 0.9，回撤 = (1.5-0.9)/1.5 = 0.4
        assert max_drawdown([0.5, -0.4]) == pytest.approx(0.4)

    def test_after_recovery(self) -> None:
        # 涨 100% → 跌 50% → 涨 200%，回撤应锁定在 50%
        # nav: 1 → 2 → 1 → 3，peak=3, current=3, 但 peak 路径经过 2，
        # 从 2 跌到 1 是 50%；之后从 1 涨到 3，peak 推进到 3，dd=0
        # 所以 max_dd=0.5
        assert max_drawdown([1.0, -0.5, 2.0]) == pytest.approx(0.5)


class TestCalmar:
    def test_empty(self) -> None:
        assert calmar_ratio([]) == 0.0

    def test_zero_mdd(self) -> None:
        assert calmar_ratio([0.01] * 10) == 0.0


# ── 胜率 / 盈亏比 ───────────────────────────────────────

class TestWinRate:
    def test_empty(self) -> None:
        assert win_rate([]) == 0.0

    def test_all_wins(self) -> None:
        assert win_rate([0.01, 0.02, 0.03]) == 1.0

    def test_half(self) -> None:
        assert win_rate([0.01, -0.01, 0.01, -0.01]) == 0.5

    def test_zero_not_counted(self) -> None:
        # 0 不算赢也不算输
        assert win_rate([0.01, 0.0, -0.01]) == 0.5


class TestProfitLossRatio:
    def test_empty(self) -> None:
        assert profit_loss_ratio([]) == 0.0

    def test_no_losses(self) -> None:
        assert profit_loss_ratio([0.01, 0.02]) == 0.0

    def test_no_wins(self) -> None:
        assert profit_loss_ratio([-0.01, -0.02]) == 0.0

    def test_basic(self) -> None:
        # 平均赢 0.10，平均亏 -0.05 → 盈亏比 2
        assert profit_loss_ratio([0.10, 0.10, -0.05, -0.05]) == pytest.approx(2.0)


# ── 综合指标 ────────────────────────────────────────────

class TestComputeMetrics:
    def test_empty(self) -> None:
        m = compute_metrics([])
        assert m["n_periods"] == 0
        assert m["total_return"] == 0.0
        assert m["sharpe_ratio"] == 0.0
        assert m["max_drawdown"] == 0.0
        assert m["win_rate"] == 0.0
        assert m["best_day"] == 0.0
        assert m["worst_day"] == 0.0

    def test_basic(self) -> None:
        r = [0.01, -0.02, 0.03, -0.01, 0.02]
        m = compute_metrics(r)
        assert m["n_periods"] == 5
        assert m["n_wins"] == 3
        assert m["n_losses"] == 2
        assert m["best_day"] == pytest.approx(0.03)
        assert m["worst_day"] == pytest.approx(-0.02)
        assert m["win_rate"] == pytest.approx(0.6)

    def test_realistic(self) -> None:
        # 1000 日仿真：年化收益 10%，年化波动 15%（放宽到 0..50% 容忍 1 sigma 噪声）
        import random
        random.seed(42)
        r = [random.gauss(0.10 / 252, 0.15 / math.sqrt(252)) for _ in range(1000)]
        m = compute_metrics(r)
        # 蒙特卡洛噪声允许 ±0.5 偏差
        assert -0.5 < m["annualized_return"] < 0.5
        assert 0.10 < m["annualized_volatility"] < 0.20


# ── 净值 / 收益 互转 ─────────────────────────────────────

class TestNavCurve:
    def test_empty(self) -> None:
        assert nav_curve([]) == [1.0]

    def test_basic(self) -> None:
        # 起点 1，+0.1 → 1.1，-0.1 → 0.99
        assert nav_curve([0.1, -0.1]) == pytest.approx([1.0, 1.1, 0.99])

    def test_start_nav(self) -> None:
        assert nav_curve([0.1], start_nav=100.0) == pytest.approx([100.0, 110.0])


class TestReturnsFromNav:
    def test_too_short(self) -> None:
        assert returns_from_nav([]) == []
        assert returns_from_nav([1.0]) == []

    def test_basic(self) -> None:
        # [1.0, 1.1, 0.99] → [0.1, -0.1]
        assert returns_from_nav([1.0, 1.1, 0.99]) == pytest.approx([0.1, -0.1])

    def test_zero_prev(self) -> None:
        # prev <= 0 → 该点返 0
        assert returns_from_nav([0.0, 1.0]) == [0.0]

    def test_roundtrip(self) -> None:
        r = [0.01, -0.02, 0.03]
        nav = nav_curve(r)
        recovered = returns_from_nav(nav)
        assert recovered == pytest.approx(r)
