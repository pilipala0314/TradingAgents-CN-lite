"""回测引擎 e2e 测试（mock DataRouter，无外网）。

覆盖：
- 10 只股票 × 6 个月 × momentum_20d 完整跑一次
- 输出指标合理性（total_return、sharpe、max_drawdown、nav 单调递增起点）
- skipped_symbols 在数据完整时为空
- 调仓记录数 = 调仓日数 - 1（首日不调仓）
- 4 个内置策略都能跑通（smoke test）
- 边界：所有股票都跳过的异常路径
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from services.backtest_engine import BacktestConfig, BacktestEngine
from services.strategies import STRATEGY_REGISTRY

# ── helpers ─────────────────────────────────────────────────────

def _make_bars(
    start: date, n_days: int, base_price: float, daily_drift: float, seed: int
) -> list[dict[str, Any]]:
    """生成确定性 K 线（线性 trend + 伪随机噪声）。"""
    import random

    rng = random.Random(seed)
    bars: list[dict[str, Any]] = []
    px = base_price
    for i in range(n_days):
        d = start + timedelta(days=i)
        # 噪声 ±1.5%，累加到 drift
        noise = rng.uniform(-0.015, 0.015)
        ret = daily_drift + noise
        close = px * (1.0 + ret)
        bars.append({"date": d, "close": round(close, 4)})
        px = close
    return bars


def _build_mock_router(bars_by_symbol: dict[str, list[dict[str, Any]]]) -> MagicMock:
    """构造 mock DataRouter：get_daily_bars 按 symbol 返对应 bars。"""
    router = MagicMock()

    def fake_route(method: str, *args: Any, **kwargs: Any) -> Any:
        if method == "get_daily_bars":
            sym = kwargs.get("symbol") or (args[0] if args else None)
            return bars_by_symbol.get(sym, [])
        raise ValueError(f"未 mock 的 method: {method}")

    router.route = MagicMock(side_effect=fake_route)
    return router


def _build_e2e_data(n_symbols: int = 10, n_days: int = 120) -> dict[str, list[dict[str, Any]]]:
    """10 只股票：5 只上涨 drift=+0.002，5 只下跌 drift=-0.001（确定性）。"""
    start = date(2025, 1, 2)
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for i in range(n_symbols):
        sym = f"{600000 + i:06d}"
        # 偶数索引上涨，奇数索引下跌 → 策略能筛出 top N
        drift = 0.002 if i % 2 == 0 else -0.001
        base = 10.0 + i * 0.5
        bars_by_symbol[sym] = _make_bars(start, n_days, base, drift, seed=42 + i)
    return bars_by_symbol


# ── 主要 e2e ────────────────────────────────────────────────────

class TestBacktestE2E:
    def test_momentum_20d_10_symbols_6_months(self) -> None:
        """10 只 × 120 个交易日 × momentum_20d 完整跑一次。"""
        bars_by_symbol = _build_e2e_data(n_symbols=10, n_days=120)
        router = _build_mock_router(bars_by_symbol)
        engine = BacktestEngine(router=router)

        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=list(bars_by_symbol.keys()),
            start_date=date(2025, 1, 2),
            end_date=date(2025, 5, 1),  # 120 天的窗口
            initial_capital=1_000_000.0,
            top_n=5,
            rebalance_freq="weekly",
        )
        result = engine.run(cfg)

        # 基本结构
        assert result.config == cfg
        assert len(result.nav) == 121  # n_periods + 1（含起点 1.0）
        assert len(result.returns) == 120
        assert result.nav[0] == pytest.approx(1.0, rel=1e-9)
        assert all(v > 0 for v in result.nav), "NAV 不应为负或 0"
        assert all(not math.isnan(v) for v in result.nav), "NAV 不应含 NaN"

        # 指标合理性（不在 NaN / inf 区间内）
        m = result.metrics
        for k in ("total_return", "annualized_return", "annualized_volatility",
                 "sharpe_ratio", "calmar_ratio", "max_drawdown", "win_rate",
                 "profit_loss_ratio"):
            assert k in m, f"指标 {k} 缺失"
            v = m[k]
            assert isinstance(v, (int, float)), f"指标 {k} 不是数值"
            assert not math.isnan(v), f"指标 {k} = NaN"
            assert math.isfinite(v), f"指标 {k} = inf"
        # 弱边界：单次 4 个月回测，总收益不可能超过 ±500%
        assert -5.0 < m["total_return"] < 5.0, f"total_return 越界: {m['total_return']}"
        assert 0.0 <= m["max_drawdown"] <= 1.0, f"max_drawdown 越界: {m['max_drawdown']}"
        assert 0.0 <= m["win_rate"] <= 1.0, f"win_rate 越界: {m['win_rate']}"

        # skipped 应为空
        assert result.skipped_symbols == []

        # 调仓记录数：每周一次，120 个交易日 ≈ 17-30 次
        # （具体取决于 isocalendar 对齐和首日不计）
        assert 15 <= len(result.trades) <= 30, (
            f"调仓次数异常: {len(result.trades)}"
        )
        # 调仓记录字段
        for t in result.trades[:3]:
            assert "date" in t
            assert "sold" in t
            assert "bought" in t
            assert "scores_n" in t
            assert "selected_n" in t

    def test_4_strategies_all_smoke(self) -> None:
        """4 个内置策略都能跑通（smoke test）。"""
        bars_by_symbol = _build_e2e_data(n_symbols=10, n_days=120)
        router = _build_mock_router(bars_by_symbol)
        engine = BacktestEngine(router=router)

        for name in STRATEGY_REGISTRY:
            cfg = BacktestConfig(
                strategy_name=name,
                symbols=list(bars_by_symbol.keys()),
                start_date=date(2025, 1, 2),
                end_date=date(2025, 5, 1),
                initial_capital=1_000_000.0,
                top_n=5,
                rebalance_freq="weekly",
            )
            result = engine.run(cfg)
            assert len(result.nav) == 121
            assert not math.isnan(result.metrics["total_return"])
            assert -5.0 < result.metrics["total_return"] < 5.0

    def test_all_symbols_skipped_raises(self) -> None:
        """所有股票都拉不到数据时抛 RuntimeError。"""
        # 全部返空（< 30 根）
        empty = _build_mock_router({})  # router.route("get_daily_bars", ...) → []
        engine = BacktestEngine(router=empty)
        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=["600000", "600001"],
            start_date=date(2025, 1, 2),
            end_date=date(2025, 5, 1),
            top_n=5,
            rebalance_freq="weekly",
        )
        with pytest.raises(RuntimeError, match="所有股票拉数据均失败"):
            engine.run(cfg)

    def test_partial_skip_records_skipped(self) -> None:
        """部分股票数据不足时，结果.skipped_symbols 记录。"""
        bars_by_symbol = _build_e2e_data(n_symbols=10, n_days=120)
        # 拿掉 2 只
        del bars_by_symbol["600000"]
        del bars_by_symbol["600001"]
        router = _build_mock_router(bars_by_symbol)
        engine = BacktestEngine(router=router)
        cfg = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=["600000", "600001", "600002", "600003", "600004"],
            start_date=date(2025, 1, 2),
            end_date=date(2025, 5, 1),
            top_n=5,
            rebalance_freq="weekly",
        )
        result = engine.run(cfg)
        # 600000/600001 拉不到数据被跳过
        assert "600000" in result.skipped_symbols
        assert "600001" in result.skipped_symbols
        # 其余 3 只跑成功
        assert len(result.nav) == 121

    def test_daily_vs_weekly_rebalance_counts(self) -> None:
        """daily / weekly 调仓次数应不同（daily > weekly）。"""
        bars_by_symbol = _build_e2e_data(n_symbols=5, n_days=60)
        router = _build_mock_router(bars_by_symbol)
        engine = BacktestEngine(router=router)

        cfg_daily = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=list(bars_by_symbol.keys()),
            start_date=date(2025, 1, 2),
            end_date=date(2025, 3, 2),
            top_n=3,
            rebalance_freq="daily",
        )
        cfg_weekly = BacktestConfig(
            strategy_name="momentum_20d",
            symbols=list(bars_by_symbol.keys()),
            start_date=date(2025, 1, 2),
            end_date=date(2025, 3, 2),
            top_n=3,
            rebalance_freq="weekly",
        )
        r_daily = engine.run(cfg_daily)
        r_weekly = engine.run(cfg_weekly)
        assert len(r_daily.trades) > len(r_weekly.trades)
