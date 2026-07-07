"""回测归因分析 — P4.5。

不做的事情：
- 不引入新数据源（不复用 sector/factor 维度的真实数据）
- 不重算收益（用 BacktestResult 现有字段 + 调用方补 bars_by_symbol）

做的事：
1. **持仓归因**（compute_position_attribution）
   - top N 持仓 vs 同期全池（all_returns_by_symbol）的累计收益对比
   - 输出：top N mean return / bottom mean return / active return
2. **Score IC 归因**（compute_score_ic）
   - 在每个调仓日，对每只股票算 score + 该股未来 N 日收益
   - 计算 Spearman Rank IC + Pearson IC
   - 衡量策略的"预测力"
3. **调仓段归因**（compute_rebalance_attribution）
   - 每次调仓的换手率 = (sold + bought) / (2 * top_n)
   - 每段（两次调仓之间）的累计收益贡献
   - 段平均收益 / 段换手率 → 调仓效率

设计原则：
- 纯函数，输入 BacktestResult + (bars_by_symbol, strategy, all_returns_by_symbol)
- 不修改 BacktestResult；只产出 attribution dict
- 容错：bars_by_symbol 缺 / strategy 缺 / 数据不足 → 返 None 而非抛错
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from services.backtest_engine import BacktestResult
    from services.strategies import Strategy

# 运行时仍需要 get_strategy（仅策略注册表查询，无循环风险）
from services.strategies import get_strategy


# ── 工具：构造标的 → 日收益 map + 累计收益 ─────────────────────
def _build_symbol_returns_map(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> dict[str, list[tuple[date, float]]]:
    """把 bars 序列按标的转成 (date, daily_return) 列表。

    daily_return = close(t) / close(t-1) - 1，第 0 根为 0。
    """
    out: dict[str, list[tuple[date, float]]] = {}
    for sym, bars in bars_by_symbol.items():
        series: list[tuple[date, float]] = []
        for i, b in enumerate(bars):
            if i == 0:
                series.append((b["date"], 0.0))
                continue
            c_cur = float(b["close"])
            c_prev = float(bars[i - 1]["close"])
            r = (c_cur / c_prev - 1.0) if c_prev > 0 else 0.0
            series.append((b["date"], r))
        out[sym] = series
    return out


def _build_symbol_total_return(
    bars_by_symbol: dict[str, list[dict[str, Any]]],
) -> dict[str, float]:
    """把 bars 序列按标的算全期累计收益。"""
    out: dict[str, float] = {}
    for sym, bars in bars_by_symbol.items():
        if not bars:
            out[sym] = 0.0
            continue
        c_first = float(bars[0]["close"])
        c_last = float(bars[-1]["close"])
        out[sym] = (c_last / c_first - 1.0) if c_first > 0 else 0.0
    return out


# ── 1. 持仓归因：top N vs 全池 ─────────────────────────────
def compute_position_attribution(
    result: BacktestResult,
    bars_by_symbol: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """持仓归因：top N 持仓股的累计收益 vs 全池平均。

    需求 bars_by_symbol：用于算每只股票的全期累计收益。

    Returns:
        {
            "top_n":                  int,
            "n_holding_periods":      int,   # 调仓次数
            "holding_symbols":        list[str],  # 调入次数最多的 top N（不一定与最后一次完全一致）
            "top_n_total_returns":    list[float],  # 各持仓股全期累计收益
            "top_n_avg_return":       float,  # 持仓股平均累计收益
            "all_pool_avg_return":    float | None,  # 全池平均累计收益（需 bars_by_symbol）
            "active_return":          float | None,  # 持仓 - 全池
            "hit_rate":               float,  # 持仓股中收益 > 0 的比例
        }
    """
    trades = result.trades or []
    if not trades:
        return {"top_n": result.config.top_n, "n_holding_periods": 0, "holding_symbols": [], "top_n_total_returns": [], "top_n_avg_return": 0.0, "all_pool_avg_return": None, "active_return": None, "hit_rate": 0.0}

    # 调入次数最多的 top N（频次权重）
    hold_count: dict[str, int] = defaultdict(int)
    for t in trades:
        for sym in (t.get("bought") or []):
            hold_count[sym] += 1
    # 排序取 top N
    top_n = result.config.top_n
    top_holdings = sorted(hold_count.items(), key=lambda x: -x[1])[:top_n]
    holding_symbols = [s for s, _ in top_holdings]

    # 累计收益
    symbol_returns: dict[str, float] = {}
    if bars_by_symbol:
        symbol_returns = _build_symbol_total_return(bars_by_symbol)
    top_returns: list[float] = []
    for sym in holding_symbols:
        if sym in symbol_returns:
            top_returns.append(symbol_returns[sym])
        elif bars_by_symbol and sym in bars_by_symbol:
            # 兜底：用 bars 算
            bars = bars_by_symbol[sym]
            if bars:
                c_first = float(bars[0]["close"])
                c_last = float(bars[-1]["close"])
                top_returns.append(c_last / c_first - 1.0 if c_first > 0 else 0.0)
    top_avg = sum(top_returns) / len(top_returns) if top_returns else 0.0
    hit_rate = sum(1 for r in top_returns if r > 0) / len(top_returns) if top_returns else 0.0

    # 全池平均
    all_pool_avg: float | None = None
    active: float | None = None
    if bars_by_symbol:
        all_returns = list(symbol_returns.values()) or [
            _build_symbol_total_return({s: bars})[s] for s, bars in bars_by_symbol.items() if bars
        ]
        if all_returns:
            all_pool_avg = sum(all_returns) / len(all_returns)
            active = top_avg - all_pool_avg

    return {
        "top_n": top_n,
        "n_holding_periods": len(trades),
        "holding_symbols": holding_symbols,
        "top_n_total_returns": top_returns,
        "top_n_avg_return": top_avg,
        "all_pool_avg_return": all_pool_avg,
        "active_return": active,
        "hit_rate": hit_rate,
    }


# ── 2. Score IC 归因：策略的"预测力" ────────────────────────────
def _spearman_rank_corr(xs: list[float], ys: list[float]) -> float | None:
    """Spearman Rank Correlation（无 numpy，手写）。"""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    # 1) rank（tie 取均值）
    def _rank(values: list[float]) -> list[float]:
        n = len(values)
        sorted_idx = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and values[sorted_idx[j + 1]] == values[sorted_idx[i]]:
                j += 1
            avg_rank = (i + j) / 2 + 1  # 1-based
            for k in range(i, j + 1):
                ranks[sorted_idx[k]] = avg_rank
            i = j + 1
        return ranks
    rx = _rank(xs)
    ry = _rank(ys)
    # 2) Pearson on ranks
    n = len(rx)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    cov = sum((rx[i] - mean_x) * (ry[i] - mean_y) for i in range(n))
    var_x = sum((r - mean_x) ** 2 for r in rx)
    var_y = sum((r - mean_y) ** 2 for r in ry)
    denom = math.sqrt(var_x * var_y)
    if denom < 1e-12:
        return 0.0
    return cov / denom


def _pearson_corr(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    var_x = sum((x - mx) ** 2 for x in xs)
    var_y = sum((y - my) ** 2 for y in ys)
    denom = math.sqrt(var_x * var_y)
    if denom < 1e-12:
        return 0.0
    return cov / denom


def compute_score_ic(
    result: BacktestResult,
    bars_by_symbol: dict[str, list[dict[str, Any]]],
    strategy: Strategy | None = None,
    horizon: int = 10,
) -> dict[str, Any]:
    """Score IC 归因：在每个调仓日，算 score 与未来 horizon 日收益的 RankIC。

    Args:
        result:         BacktestResult（需有 rebalance_dates）
        bars_by_symbol: 回测用的 bars（用于复算 score + future return）
        strategy:       策略实例（None = 从 result.config.strategy_name 拉）
        horizon:        未来 N 日收益窗口（默认 10）

    Returns:
        {
            "horizon":         int,
            "n_periods":       int,           # 有效调仓期数
            "ic_series":       list[float | None],  # 每次调仓日的 RankIC
            "ic_mean":         float | None,  # IC 均值
            "ic_std":          float | None,
            "ic_ir":           float | None,  # IC 均值 / IC 标准差
            "ic_hit_rate":     float | None,  # IC > 0 的比例
            "pearson_mean":    float | None,
            "n_obs_total":     int,           # score-future 总配对数
        }
    """
    if not result.rebalance_dates or not bars_by_symbol:
        return _empty_ic_result(horizon)
    if strategy is None:
        strategy = get_strategy(result.config.strategy_name)

    sym_returns = _build_symbol_returns_map(bars_by_symbol)
    min_lookback = strategy.min_lookback()

    ic_series: list[float | None] = []
    pearson_series: list[float | None] = []
    n_obs_total = 0

    for rb_date in result.rebalance_dates:
        scores: list[float] = []
        futures: list[float] = []
        for sym, bars in bars_by_symbol.items():
            # 找 rb_date 在 bars 的索引
            bar_idx = None
            for j, b in enumerate(bars):
                if b["date"] >= rb_date:
                    bar_idx = j
                    break
            if bar_idx is None or bar_idx < min_lookback:
                continue
            s = strategy.score(bars, bar_idx)
            if s is None:
                continue
            try:
                f_score = float(s)
            except (TypeError, ValueError):
                continue
            if math.isnan(f_score):
                continue
            # 未来 horizon 日累计收益：从 rb_date + 1 到 +horizon
            fut = _future_return(sym_returns.get(sym, []), rb_date, horizon)
            if fut is None:
                continue
            scores.append(f_score)
            futures.append(fut)
        if len(scores) < 2:
            ic_series.append(None)
            pearson_series.append(None)
            continue
        ic = _spearman_rank_corr(scores, futures)
        pearson = _pearson_corr(scores, futures)
        ic_series.append(ic)
        pearson_series.append(pearson)
        n_obs_total += len(scores)

    valid_ic = [x for x in ic_series if x is not None]
    valid_pearson = [x for x in pearson_series if x is not None]
    ic_mean = sum(valid_ic) / len(valid_ic) if valid_ic else None
    ic_std = _std(valid_ic) if len(valid_ic) >= 2 else None
    ic_ir = (ic_mean / ic_std) if (ic_mean is not None and ic_std and ic_std > 1e-12) else None
    ic_hit_rate = sum(1 for x in valid_ic if x > 0) / len(valid_ic) if valid_ic else None
    pearson_mean = sum(valid_pearson) / len(valid_pearson) if valid_pearson else None

    return {
        "horizon": horizon,
        "n_periods": len([x for x in ic_series if x is not None]),
        "ic_series": ic_series,
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "ic_ir": ic_ir,
        "ic_hit_rate": ic_hit_rate,
        "pearson_mean": pearson_mean,
        "n_obs_total": n_obs_total,
    }


def _future_return(
    series: list[tuple[date, float]], start: date, horizon: int
) -> float | None:
    """从 start 之后第一个交易日开始，累计 horizon 个交易日的收益。

    数据不足（实际取到 < horizon 个未来交易日）返 None。
    """
    if not series or horizon <= 0:
        return None
    in_window: list[tuple[date, float]] = []
    for d, r in series:
        if d <= start:
            continue
        in_window.append((d, r))
        if len(in_window) >= horizon:
            break
    # 要求取满 horizon 个未来交易日，否则视为数据不足
    if len(in_window) < horizon:
        return None
    nav = 1.0
    for _, r in in_window:
        nav *= 1.0 + r
    return nav - 1.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / len(values)
    return math.sqrt(var)


def alpha_beta(
    strategy_returns: list[float],
    benchmark_returns: list[float],
) -> dict[str, float]:
    """线性回归 alpha / beta + 相关系数（P2.7 benchmark 用）。

    公式：
        beta  = Cov(s, b) / Var(b)
        alpha = mean(s - beta * b)        （日度 alpha，未年化）
        correlation = Cov(s, b) / (std_s * std_b)

    Returns:
        dict 含 alpha / beta / correlation / n_obs。数据不足返 0/0/0/0。
    """
    n = min(len(strategy_returns), len(benchmark_returns))
    if n < 2:
        return {"alpha": 0.0, "beta": 0.0, "correlation": 0.0, "n_obs": n}
    s = [float(x) for x in strategy_returns[:n]]
    b = [float(x) for x in benchmark_returns[:n]]
    mean_s = sum(s) / n
    mean_b = sum(b) / n
    cov = sum((s[i] - mean_s) * (b[i] - mean_b) for i in range(n)) / n
    var_b = sum((b[i] - mean_b) ** 2 for i in range(n)) / n
    std_s = _std(s)
    std_b = _std(b)
    if var_b <= 0 or std_b <= 0 or std_s <= 0:
        return {"alpha": 0.0, "beta": 0.0, "correlation": 0.0, "n_obs": n}
    beta = cov / var_b
    alpha = (mean_s - beta * mean_b)
    correlation = cov / (std_s * std_b)
    return {
        "alpha": float(alpha),
        "beta": float(beta),
        "correlation": float(correlation),
        "n_obs": n,
    }


def _empty_ic_result(horizon: int) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "n_periods": 0,
        "ic_series": [],
        "ic_mean": None,
        "ic_std": None,
        "ic_ir": None,
        "ic_hit_rate": None,
        "pearson_mean": None,
        "n_obs_total": 0,
    }


# ── 3. 调仓段归因：换手率 + 段贡献 ──────────────────────────
def compute_rebalance_attribution(result: BacktestResult) -> dict[str, Any]:
    """调仓段归因：每段换手率 + 段累计收益 + 段平均日收益。

    "段" = 两次调仓日之间的窗口（第 1 段从第 1 次调仓到第 2 次调仓）。

    Returns:
        {
            "n_rebalances":   int,   # 调仓次数
            "segments": [
                {
                    "date":         str,   # 本段起始调仓日
                    "sold_n":       int,
                    "bought_n":     int,
                    "turnover":     float,  # (sold + bought) / (2 * top_n)
                    "n_days":       int,   # 段内交易日数
                    "cum_return":   float,  # 段累计收益
                    "avg_daily":    float,
                },
                ...
            ],
            "avg_turnover":     float,
            "max_turnover":     float,
            "best_segment":     dict | None,  # 累计收益最大段
            "worst_segment":    dict | None,
        }
    """
    trades = result.trades or []
    rebalance_dates = result.rebalance_dates or []
    returns = result.returns or []
    if not trades or not rebalance_dates or not returns:
        return {
            "n_rebalances": 0,
            "segments": [],
            "avg_turnover": 0.0,
            "max_turnover": 0.0,
            "best_segment": None,
            "worst_segment": None,
        }

    # 用 trading_dates 长度 = returns 长度
    n_periods = len(returns)
    top_n = result.config.top_n
    segments: list[dict[str, Any]] = []
    turnovers: list[float] = []

    # 段边界：[i, j) — 调仓 i 之后到调仓 j 之前
    # 第一个调仓在 trading_dates 中的位置由 rebalance_dates 决定；
    # 简化：用 trade 列表索引对齐（trades[i] 对应第 i 次调仓）
    for i, t in enumerate(trades):
        sold_n = len(t.get("sold") or [])
        bought_n = len(t.get("bought") or [])
        turnover = (sold_n + bought_n) / (2.0 * max(top_n, 1))
        turnovers.append(turnover)
        # 段累计收益 = 调仓后到下一次调仓前的累计收益
        # 简化：用 trade 在 trades 列表中的索引近似
        # 实际累计区间 = [rebalance_date(i), rebalance_date(i+1))，长度 = rebalance_dates[i+1] 与 rebalance_dates[i] 间隔的交易日数
        n_days = 0
        if i + 1 < len(rebalance_dates):
            # 段长度 = 两次调仓日之间的交易日数（粗略用下一调仓日-本次调仓日 / 1.5 估算）
            d_cur = rebalance_dates[i]
            d_next = rebalance_dates[i + 1]
            n_days = max(1, (d_next - d_cur).days // 1)  # 简化
        else:
            # 最后一段：从最后一次调仓到回测结束
            n_days = max(1, n_periods - i)
        seg_return = sum(returns[i: i + n_days]) if i + n_days <= n_periods else sum(returns[i:])
        avg_daily = seg_return / n_days if n_days > 0 else 0.0
        segments.append({
            "date": str(t.get("date", rebalance_dates[i] if i < len(rebalance_dates) else "")),
            "sold_n": sold_n,
            "bought_n": bought_n,
            "turnover": turnover,
            "n_days": n_days,
            "cum_return": seg_return,
            "avg_daily": avg_daily,
        })

    avg_turnover = sum(turnovers) / len(turnovers) if turnovers else 0.0
    max_turnover = max(turnovers) if turnovers else 0.0
    best = max(segments, key=lambda s: s["cum_return"]) if segments else None
    worst = min(segments, key=lambda s: s["cum_return"]) if segments else None

    return {
        "n_rebalances": len(trades),
        "segments": segments,
        "avg_turnover": avg_turnover,
        "max_turnover": max_turnover,
        "best_segment": best,
        "worst_segment": worst,
    }


# ── 4. 全量归因：一次出 3 类 ─────────────────────────────
def compute_full_attribution(
    result: BacktestResult,
    bars_by_symbol: dict[str, list[dict[str, Any]]] | None = None,
    strategy: Strategy | None = None,
    horizon: int = 10,
) -> dict[str, Any]:
    """一次跑 3 类归因并合并输出（API / 前端友好）。

    Returns:
        {
            "position":       PositionAttribution,
            "score_ic":       ScoreICAttribution,
            "rebalance":      RebalanceAttribution,
        }
    """
    return {
        "position": compute_position_attribution(result, bars_by_symbol),
        "score_ic": compute_score_ic(result, bars_by_symbol or {}, strategy, horizon),
        "rebalance": compute_rebalance_attribution(result),
    }
