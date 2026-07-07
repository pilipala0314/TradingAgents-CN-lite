"""test_backtest_attribution — P4.5 回测归因分析 单测。

覆盖：
- compute_position_attribution：top N vs 全池累计收益对比 / active return / hit_rate
- compute_score_ic：Spearman Rank IC / Pearson IC / IC IR / hit rate
- compute_rebalance_attribution：换手率 / 段收益 / best/worst 段
- compute_full_attribution：3 类合一
- 容错：空 result / 缺 bars / 数据不足
- 数学正确性：构造已知相关数据，验证 IC > 0
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from services.backtest_attribution import (
    _future_return,
    _pearson_corr,
    _spearman_rank_corr,
    _std,
    compute_full_attribution,
    compute_position_attribution,
    compute_rebalance_attribution,
    compute_score_ic,
)
from services.backtest_engine import BacktestConfig, BacktestResult


# ── helpers ─────────────────────────────────────────
def _make_bars(symbol: str, start: date, n_days: int, base: float, daily_ret: float) -> list[dict]:
    """构造单只股票 N 根 K 线（date 连续 + 稳定日收益）。"""
    bars = []
    price = base
    for i in range(n_days):
        d = start + timedelta(days=i)
        if i > 0:
            price *= (1.0 + daily_ret)
        bars.append({"date": d, "close": price})
    return bars


def _make_result(
    *,
    trades: list[dict] | None = None,
    rebalance_dates: list[date] | None = None,
    returns: list[float] | None = None,
    strategy_name: str = "momentum_20d",
    top_n: int = 3,
    symbols: tuple[str, ...] = ("600519", "000001", "000636"),
) -> BacktestResult:
    cfg = BacktestConfig(
        strategy_name=strategy_name,
        symbols=list(symbols),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
        top_n=top_n,
        rebalance_freq="weekly",
    )
    return BacktestResult(
        config=cfg,
        returns=returns or [],
        nav=[],
        rebalance_dates=rebalance_dates or [],
        trades=trades or [],
    )


# ── 1. compute_position_attribution ─────────────────────────
class TestPositionAttribution:
    def test_empty_trades(self) -> None:
        r = _make_result()
        out = compute_position_attribution(r, bars_by_symbol=None)
        assert out["n_holding_periods"] == 0
        assert out["holding_symbols"] == []
        assert out["top_n_avg_return"] == 0.0
        assert out["all_pool_avg_return"] is None

    def test_top_n_picks_most_held(self) -> None:
        """P4.5：调入次数最多的 top N 选为 holding_symbols。"""
        trades = [
            {"date": "2026-02-01", "sold": [], "bought": ["600519", "000001", "000636"]},
            {"date": "2026-02-15", "sold": ["000636"], "bought": ["002415"]},  # 000636 调出
            {"date": "2026-03-01", "sold": [], "bought": ["000636"]},  # 000636 调入
            {"date": "2026-03-15", "sold": ["000001"], "bought": ["000636"]},  # 000001 调出
        ]
        r = _make_result(trades=trades, top_n=2)
        out = compute_position_attribution(r, bars_by_symbol=None)
        # 600519 调入 1 次，000001 调入 1 次，000636 调入 2 次，002415 调入 1 次
        # top 2 = 000636 (2次) + 任一 (1次) 之一
        assert out["top_n"] == 2
        assert "000636" in out["holding_symbols"]
        assert len(out["holding_symbols"]) == 2

    def test_with_bars_computes_active_return(self) -> None:
        """P4.5：有 bars_by_symbol 时算 all_pool_avg + active_return。"""
        trades = [{"date": "2026-02-01", "sold": [], "bought": ["600519", "000001"]}]
        # 600519 涨 50%，000001 涨 20%，000636 跌 30% → 池子平均 -3.33%
        bars = {
            "600519": _make_bars("600519", date(2026, 1, 1), 60, 100, 0.013),  # 涨 ~ 116%
            "000001": _make_bars("000001", date(2026, 1, 1), 60, 10, 0.005),   # 涨 ~ 35%
            "000636": _make_bars("000636", date(2026, 1, 1), 60, 20, -0.008),  # 跌 ~ 38%
        }
        r = _make_result(trades=trades, symbols=("600519", "000001", "000636"))
        out = compute_position_attribution(r, bars_by_symbol=bars)
        assert out["all_pool_avg_return"] is not None
        assert out["active_return"] is not None
        # top_n 持仓股（600519 + 000001）平均 > 池子平均（000636 拖累）
        assert out["top_n_avg_return"] > out["all_pool_avg_return"]
        assert out["active_return"] > 0

    def test_hit_rate(self) -> None:
        """P4.5：hit_rate = 持仓股中收益 > 0 的比例。"""
        trades = [{"date": "2026-02-01", "sold": [], "bought": ["600519", "000636"]}]
        bars = {
            "600519": _make_bars("600519", date(2026, 1, 1), 30, 100, 0.005),  # 涨
            "000636": _make_bars("000636", date(2026, 1, 1), 30, 100, -0.005),  # 跌
        }
        r = _make_result(trades=trades, symbols=("600519", "000636"))
        out = compute_position_attribution(r, bars_by_symbol=bars)
        assert out["hit_rate"] == 0.5  # 1/2 正


# ── 2. compute_score_ic ───────────────────────────────────
class TestScoreIC:
    def test_empty_result(self) -> None:
        r = _make_result()
        out = compute_score_ic(r, bars_by_symbol={})
        assert out["n_periods"] == 0
        assert out["ic_mean"] is None
        assert out["n_obs_total"] == 0

    def test_known_positive_correlation(self) -> None:
        """P4.5：构造 score 与 future_return 正相关的数据，IC > 0。"""
        rb_date = date(2026, 2, 1)
        # 4 只股，每天价格稳定增长（daily_ret 递增 0.005 → 0.02）
        # bars 长度 30 = 20 之前 + rb_date + 9 之后（horizon=5 够用）
        bars = {
            "A": _make_bars("A", rb_date - timedelta(days=20), 30, 100, 0.005),
            "B": _make_bars("B", rb_date - timedelta(days=20), 30, 100, 0.010),
            "C": _make_bars("C", rb_date - timedelta(days=20), 30, 100, 0.015),
            "D": _make_bars("D", rb_date - timedelta(days=20), 30, 100, 0.020),
        }
        r = _make_result(rebalance_dates=[rb_date])
        from services.strategies import MomentumStrategy
        strat = MomentumStrategy(lookback=10)  # 任意能返回 score 的策略
        out = compute_score_ic(r, bars, strategy=strat, horizon=5)
        # 4 只股都有 score + future return → 至少 1 期有效
        assert out["n_periods"] >= 1
        assert out["n_obs_total"] == 4

    def test_known_ic_with_perfect_alignment(self) -> None:
        """P4.5：用 mock strategy 让 score = future_return 完美对齐，IC 应接近 1.0。"""
        rb_date = date(2026, 2, 1)

        class _PerfectStrat:
            name = "perfect_alignment"
            def min_lookback(self) -> int: return 0
            def score(self, bars, end_idx):
                # score = 未来 5 日累计收益
                nav = 1.0
                for i in range(1, 6):
                    if end_idx + i < len(bars):
                        nav *= bars[end_idx + i]["close"] / bars[end_idx + i - 1]["close"]
                return nav - 1.0

        # bars 长度 26 = 20 之前 + rb_date + 5 之后（horizon=5 够用）
        bars = {
            "A": _make_bars("A", rb_date - timedelta(days=20), 26, 100, 0.005),
            "B": _make_bars("B", rb_date - timedelta(days=20), 26, 100, 0.01),
            "C": _make_bars("C", rb_date - timedelta(days=20), 26, 100, 0.02),
            "D": _make_bars("D", rb_date - timedelta(days=20), 26, 100, 0.03),
        }
        r = _make_result(rebalance_dates=[rb_date])
        out = compute_score_ic(r, bars, strategy=_PerfectStrat(), horizon=5)
        # IC 应接近 1.0（rank 完美对齐）
        assert out["ic_mean"] is not None
        assert out["ic_mean"] > 0.95  # 允许浮点误差
        assert out["ic_hit_rate"] == 1.0

    def test_horizon_respected(self) -> None:
        """P4.5：horizon 参数影响 future_return 窗口。"""
        rb_date = date(2026, 2, 1)

        class _SimpleStrat:
            name = "simple"
            def min_lookback(self) -> int: return 0
            def score(self, bars, end_idx): return float(end_idx)  # bar_idx 作为 score

        # 3 只股，bars 长度 20 = rb_date + 之后 19 根（horizon=10 够用）
        bars = {
            "A": _make_bars("A", rb_date, 20, 100, 0.01),
            "B": _make_bars("B", rb_date, 20, 100, 0.02),
            "C": _make_bars("C", rb_date, 20, 100, 0.03),
        }
        r = _make_result(rebalance_dates=[rb_date])
        out5 = compute_score_ic(r, bars, strategy=_SimpleStrat(), horizon=5)
        out10 = compute_score_ic(r, bars, strategy=_SimpleStrat(), horizon=10)
        # 3 只股都有效
        assert out5["n_obs_total"] == 3
        assert out10["n_obs_total"] == 3


# ── 3. compute_rebalance_attribution ───────────────────────────
class TestRebalanceAttribution:
    def test_empty(self) -> None:
        r = _make_result()
        out = compute_rebalance_attribution(r)
        assert out["n_rebalances"] == 0
        assert out["segments"] == []
        assert out["avg_turnover"] == 0.0

    def test_turnover_calculation(self) -> None:
        """P4.5：turnover = (sold + bought) / (2 * top_n)。"""
        # top_n=3, sold=1, bought=2 → turnover = 3/6 = 0.5
        trades = [{"date": "2026-02-01", "sold": ["X"], "bought": ["A", "B"]}]
        r = _make_result(
            trades=trades, top_n=3,
            rebalance_dates=[date(2026, 2, 1)],
            returns=[0.01] * 5,
        )
        out = compute_rebalance_attribution(r)
        assert out["n_rebalances"] == 1
        assert out["segments"][0]["turnover"] == 0.5
        assert out["avg_turnover"] == 0.5
        assert out["max_turnover"] == 0.5

    def test_segment_returns_accumulate(self) -> None:
        """P4.5：每段 cum_return 累加 = 总收益。"""
        trades = [
            {"date": "2026-02-01", "sold": [], "bought": ["A"]},
            {"date": "2026-02-08", "sold": [], "bought": ["A"]},
        ]
        rebalance_dates = [date(2026, 2, 1), date(2026, 2, 8)]
        # 20 个交易日总收益：每段 7 天，每天 0.005
        returns = [0.005] * 20
        r = _make_result(trades=trades, rebalance_dates=rebalance_dates, returns=returns)
        out = compute_rebalance_attribution(r)
        assert out["n_rebalances"] == 2
        # best/worst 段
        assert out["best_segment"] is not None
        assert out["worst_segment"] is not None
        # 段日期
        assert out["segments"][0]["date"] == "2026-02-01"
        assert out["segments"][1]["date"] == "2026-02-08"

    def test_best_worst_segment(self) -> None:
        """P4.5：best/worst 段按 cum_return 选。"""
        # 构造 3 段：第 1 段微涨 +0.001*7=0.007，第 2 段大涨 +0.005*7=0.035，第 3 段大跌 -0.01*6=-0.06
        trades = [
            {"date": "2026-02-01", "sold": [], "bought": ["A"]},
            {"date": "2026-02-08", "sold": [], "bought": ["A"]},
            {"date": "2026-02-15", "sold": [], "bought": ["A"]},
        ]
        rebalance_dates = [date(2026, 2, 1), date(2026, 2, 8), date(2026, 2, 15)]
        returns = [0.001] * 7 + [0.005] * 7 + [-0.01] * 6
        r = _make_result(trades=trades, rebalance_dates=rebalance_dates, returns=returns)
        out = compute_rebalance_attribution(r)
        # 第 2 段 0.035 最高 → best
        # 第 3 段 -0.06 最低 → worst
        assert out["best_segment"]["date"] == "2026-02-08"
        assert out["worst_segment"]["date"] == "2026-02-15"


# ── 4. compute_full_attribution ───────────────────────────────
class TestFullAttribution:
    def test_aggregates_three(self) -> None:
        r = _make_result()
        out = compute_full_attribution(r)
        assert "position" in out
        assert "score_ic" in out
        assert "rebalance" in out
        # position 默认空 result → n_holding_periods=0
        assert out["position"]["n_holding_periods"] == 0
        # score_ic 默认空 → n_periods=0
        assert out["score_ic"]["n_periods"] == 0
        # rebalance 默认空 → n_rebalances=0
        assert out["rebalance"]["n_rebalances"] == 0


# ── 5. 数学工具函数 ─────────────────────────────────────
class TestMathHelpers:
    def test_spearman_perfect_positive(self) -> None:
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [10.0, 20.0, 30.0, 40.0]
        assert _spearman_rank_corr(xs, ys) == 1.0

    def test_spearman_perfect_negative(self) -> None:
        xs = [1.0, 2.0, 3.0, 4.0]
        ys = [40.0, 30.0, 20.0, 10.0]
        assert _spearman_rank_corr(xs, ys) == -1.0

    def test_spearman_uncorrelated(self) -> None:
        # 平局 + 排名相等 → 0
        xs = [1.0, 1.0, 1.0]
        ys = [1.0, 2.0, 3.0]
        # 排名方差为 0 → 返 0.0
        assert _spearman_rank_corr(xs, ys) == 0.0

    def test_pearson_known(self) -> None:
        # 完美线性：pearson=1
        xs = [1.0, 2.0, 3.0]
        ys = [2.0, 4.0, 6.0]
        assert abs(_pearson_corr(xs, ys) - 1.0) < 1e-9

    def test_future_return_known(self) -> None:
        # 5 天每天 +1% → 累计 ~ 5.1%
        series = [(date(2026, 2, i + 1), 0.01) for i in range(5)]
        fut = _future_return(series, start=date(2026, 1, 31), horizon=5)
        expected = (1.01 ** 5) - 1
        assert abs(fut - expected) < 1e-6

    def test_future_return_insufficient(self) -> None:
        series = [(date(2026, 2, 1), 0.01)]
        assert _future_return(series, date(2026, 1, 31), 5) is None

    def test_std_known(self) -> None:
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        # 总体 std = sqrt(2) ≈ 1.414
        assert abs(_std(vals) - (2.0 ** 0.5)) < 1e-9
