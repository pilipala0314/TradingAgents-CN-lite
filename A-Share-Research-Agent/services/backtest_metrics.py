"""回测指标计算 — 纯函数（输入日收益序列，输出 dict）。

指标：
- 累计收益率 / 年化收益率
- 年化波动率 / 夏普比率（无风险利率 0）
- 最大回撤 / Calmar 比率
- 胜率 / 盈亏比
- 收益分布（up/down 日数、最大单日盈亏）

设计原则：
- 纯函数，便于测试
- 不依赖 numpy/pandas（保持轻量）
- 输入支持任意频率（默认 252 交易日/年）
- 输入为空 / 全 0 全部容错（不抛错，返 None 或 0）
"""
from __future__ import annotations

import math
from typing import Any

# ── 单点指标 ────────────────────────────────────────────────────

def total_return(returns: list[float]) -> float:
    """累计收益率：∏(1 + r) - 1。空返 0。"""
    if not returns:
        return 0.0
    nav = 1.0
    for r in returns:
        nav *= 1.0 + r
    return nav - 1.0


def annualized_return(returns: list[float], periods_per_year: int = 252) -> float:
    """年化收益率：(1 + total)^(periods/n) - 1。空返 0。"""
    if not returns:
        return 0.0
    nav = 1.0
    for r in returns:
        nav *= 1.0 + r
    if nav <= 0:
        # 净值归零或为负，返 -1 表示完全亏损
        return -1.0
    # 浮点保护：nav 接近 1.0 时直接返 0
    if abs(nav - 1.0) < 1e-12:
        return 0.0
    return nav ** (periods_per_year / len(returns)) - 1.0


def annualized_volatility(returns: list[float], periods_per_year: int = 252) -> float:
    """年化波动率：std(returns) * sqrt(periods_per_year)。< 2 个点返 0。"""
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / n  # 总体方差（用 n）
    return math.sqrt(var) * math.sqrt(periods_per_year)


def sharpe_ratio(returns: list[float], periods_per_year: int = 252) -> float:
    """夏普比率：年化收益 / 年化波动率（无风险利率 0）。波动率为 0（容差 1e-12）时返 0。"""
    vol = annualized_volatility(returns, periods_per_year)
    if vol < 1e-12:
        return 0.0
    return annualized_return(returns, periods_per_year) / vol


def max_drawdown(returns: list[float]) -> float:
    """最大回撤：累计净值峰谷之差的最大值（正值表示亏损幅度，0..1）。空返 0。

    Returns:
        正数（0.20 = 20% 回撤）。
    """
    if not returns:
        return 0.0
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        nav *= 1.0 + r
        if nav > peak:
            peak = nav
        if peak > 0:
            dd = (peak - nav) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def calmar_ratio(returns: list[float], periods_per_year: int = 252) -> float:
    """Calmar 比率：年化收益 / 最大回撤。回撤为 0 时返 0。"""
    mdd = max_drawdown(returns)
    if mdd == 0:
        return 0.0
    return annualized_return(returns, periods_per_year) / mdd


def win_rate(returns: list[float]) -> float:
    """胜率：r > 0 的占比（r = 0 不计入分母）。空返 0。"""
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > 0)
    losses = sum(1 for r in returns if r < 0)
    denom = wins + losses
    if denom == 0:
        return 0.0
    return wins / denom


def profit_loss_ratio(returns: list[float]) -> float:
    """盈亏比：平均盈利 / 平均亏损（绝对值）。无亏损时返 0。"""
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    if not wins or not losses:
        return 0.0
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    if avg_loss == 0:
        return 0.0
    return avg_win / avg_loss


# ── 综合指标 ────────────────────────────────────────────────────

def compute_metrics(
    returns: list[float],
    *,
    periods_per_year: int = 252,
) -> dict[str, Any]:
    """一次算出全部指标。

    Args:
        returns:         日收益序列（不含首日的 0 起点；引擎层会从净值算出 ret）
        periods_per_year: 年化频率（日线 252 / 周线 52 / 月线 12）

    Returns:
        dict 含以下键（全部为 float）：
        - total_return, annualized_return
        - annualized_volatility, sharpe_ratio, calmar_ratio
        - max_drawdown
        - win_rate, profit_loss_ratio
        - n_periods, n_wins, n_losses
        - best_day, worst_day
    """
    n = len(returns)
    n_wins = sum(1 for r in returns if r > 0)
    n_losses = sum(1 for r in returns if r < 0)
    return {
        "total_return": total_return(returns),
        "annualized_return": annualized_return(returns, periods_per_year),
        "annualized_volatility": annualized_volatility(returns, periods_per_year),
        "sharpe_ratio": sharpe_ratio(returns, periods_per_year),
        "calmar_ratio": calmar_ratio(returns, periods_per_year),
        "max_drawdown": max_drawdown(returns),
        "win_rate": win_rate(returns),
        "profit_loss_ratio": profit_loss_ratio(returns),
        "n_periods": n,
        "n_wins": n_wins,
        "n_losses": n_losses,
        "best_day": max(returns) if returns else 0.0,
        "worst_day": min(returns) if returns else 0.0,
    }


# ── 净值/收益 互转 ──────────────────────────────────────────────

def nav_curve(returns: list[float], start_nav: float = 1.0) -> list[float]:
    """收益序列 → 净值序列（含起点 1.0）。空返 [start_nav]。"""
    out = [start_nav]
    nav = start_nav
    for r in returns:
        nav *= 1.0 + r
        out.append(nav)
    return out


def returns_from_nav(nav: list[float]) -> list[float]:
    """净值序列 → 收益序列（差分，n-1 个点）。长度 < 2 返空。"""
    if len(nav) < 2:
        return []
    out: list[float] = []
    for i in range(1, len(nav)):
        prev = nav[i - 1]
        if prev <= 0:
            out.append(0.0)
        else:
            out.append(nav[i] / prev - 1.0)
    return out


# ── 基准对比指标（P2.7）────────────────────────────────────────

def alpha_beta(
    strat_returns: list[float],
    bench_returns: list[float],
) -> dict[str, float | None]:
    """策略 vs 基准的 alpha / beta / correlation。

    Alpha = annualized(strat) - beta * annualized(bench)（CAPM 残差）
    Beta  = cov(strat, bench) / var(bench)
    R     = corr(strat, bench)

    对齐：截短到两者 min 长度（避免交易日不同步）。
    空 / 长度 < 2 / var(bench) ≈ 0 → 返 None 而非抛错。

    Returns:
        {
            "alpha":       float | None,  # 年化 alpha
            "beta":        float | None,
            "correlation": float | None,
            "n_obs":       int,
        }
    """
    n = min(len(strat_returns), len(bench_returns))
    if n < 2:
        return {"alpha": None, "beta": None, "correlation": None, "n_obs": n}
    s = strat_returns[-n:]
    b = bench_returns[-n:]
    mean_s = sum(s) / n
    mean_b = sum(b) / n
    cov = sum((s[i] - mean_s) * (b[i] - mean_b) for i in range(n)) / n
    var_b = sum((b[i] - mean_b) ** 2 for i in range(n)) / n
    var_s = sum((s[i] - mean_s) ** 2 for i in range(n)) / n
    beta = (cov / var_b) if var_b > 1e-12 else None
    # 相关系数
    denom = ((var_s * var_b) ** 0.5) if (var_s > 1e-12 and var_b > 1e-12) else 0.0
    corr = (cov / denom) if denom > 1e-12 else None
    # alpha（年化）
    if beta is None:
        alpha = None
    else:
        annualized_s = annualized_return(s, periods_per_year=252)
        annualized_b = annualized_return(b, periods_per_year=252)
        alpha = annualized_s - beta * annualized_b
    return {
        "alpha": alpha,
        "beta": beta,
        "correlation": corr,
        "n_obs": n,
    }
