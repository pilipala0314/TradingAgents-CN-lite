"""test_wf_mc — P5.2 walk-forward + 蒙特卡洛单测。"""
from __future__ import annotations

import math
import random

import pytest

from services.montecarlo import _dist_stats, _percentile, run_montecarlo
from services.walkforward import run_walkforward, split_folds

# ── walk-forward ────────────────────────────


def test_split_folds_anchored() -> None:
    """anchored 模式 train_start=0。"""
    folds = split_folds(100, n_folds=5, train_pct=0.6, anchored=True)
    for ts, te, vs, ve in folds:
        assert ts == 0
        assert te == vs
        assert ve > vs
    assert len(folds) == 5


def test_split_folds_rolling() -> None:
    """rolling 模式 train 长度固定（除了第一个可能被 max(0,..) 截断）。"""
    folds = split_folds(100, n_folds=5, train_pct=0.6, anchored=False)
    train_lens = [te - ts for ts, te, _, _ in folds]
    # 至少 1 个 fold
    assert len(folds) >= 1
    # 中间折 train 长度固定
    if len(folds) >= 3:
        mid = train_lens[1:-1]
        assert len(set(mid)) == 1


def test_split_folds_short_raises() -> None:
    with pytest.raises(ValueError):
        split_folds(5, n_folds=5)


def test_split_folds_invalid_pct() -> None:
    with pytest.raises(ValueError):
        split_folds(100, n_folds=5, train_pct=0)
    with pytest.raises(ValueError):
        split_folds(100, n_folds=5, train_pct=1.0)


def test_run_walkforward_known_pattern() -> None:
    """用确定性收益跑出可校验的 OOS 指标。"""
    # 50 个点：前 10 个 +0.001（正），后 40 个 -0.002（负）→ OOS 全负
    returns = [0.001] * 10 + [-0.002] * 40
    r = run_walkforward(returns, n_folds=3, train_pct=0.6, anchored=True)
    assert "folds" in r
    assert "oos_metrics" in r
    assert "summary" in r
    assert r["summary"]["n_folds"] >= 1
    # anchored + OOS 全部位于最后 → 全是负收益
    assert r["summary"]["oos_hit_rate"] == 0.0
    assert r["oos_metrics"]["annual_return"] < 0


def test_run_walkforward_summary_keys() -> None:
    r = run_walkforward([0.001] * 50 + [0.002] * 50, n_folds=4)
    assert set(r["summary"].keys()) == {"n_folds", "mean_oos_sharpe", "oos_hit_rate", "consistency"}


# ── montecarlo ────────────────────────────


def test_percentile_basic() -> None:
    assert _percentile([1, 2, 3, 4, 5], 50) == 3.0
    assert _percentile([1, 2, 3, 4, 5], 0) == 1
    assert _percentile([1, 2, 3, 4, 5], 100) == 5


def test_percentile_empty() -> None:
    assert _percentile([], 50) == 0.0


def test_dist_stats_empty() -> None:
    s = _dist_stats([])
    assert s["mean"] == 0.0


def test_dist_stats_basic() -> None:
    s = _dist_stats([1, 2, 3, 4, 5])
    assert s["min"] == 1
    assert s["max"] == 5
    assert abs(s["mean"] - 3.0) < 1e-6


def test_run_montecarlo_returns_structure() -> None:
    returns = [0.001 * (1 if i % 2 == 0 else -1) for i in range(60)]
    r = run_montecarlo(returns, n_sims=50, horizon=120, block_size=5, seed=42)
    assert "config" in r
    assert "summary" in r
    s = r["summary"]
    assert s["n_sims"] == 50
    assert s["horizon"] == 120
    assert 0.0 <= s["prob_profit"] <= 1.0
    assert "final_return" in s
    assert s["final_return"]["mean"] != 0 or s["final_return"]["std"] >= 0


def test_run_montecarlo_seed_reproducible() -> None:
    returns = [0.001 * (i % 3 - 1) for i in range(50)]
    r1 = run_montecarlo(returns, n_sims=30, seed=42)
    r2 = run_montecarlo(returns, n_sims=30, seed=42)
    assert r1["summary"]["final_return"]["mean"] == r2["summary"]["final_return"]["mean"]


def test_run_montecarlo_empty_raises() -> None:
    with pytest.raises(ValueError):
        run_montecarlo([], n_sims=10)


def test_run_montecarlo_var_cvar_logic() -> None:
    """VaR/CVaR 应为分布左尾指标（接近最小值）。"""
    # 完全负收益
    returns = [-0.01] * 50
    r = run_montecarlo(returns, n_sims=20, horizon=20, block_size=5, seed=42)
    s = r["summary"]
    # 全负 → VaR95 ≈ -0.99 量级（年化 100% 亏损）
    assert s["var_95"] < 0
    assert s["cvar_95"] < 0
    assert s["prob_profit"] == 0.0


def test_run_montecarlo_all_positive() -> None:
    returns = [0.01] * 50
    r = run_montecarlo(returns, n_sims=20, horizon=20, block_size=5, seed=42)
    assert r["summary"]["prob_profit"] == 1.0


def test_run_montecarlo_invalid_n() -> None:
    with pytest.raises(ValueError):
        run_montecarlo([0.01] * 50, n_sims=0)
