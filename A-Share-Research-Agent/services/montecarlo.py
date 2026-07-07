"""services/montecarlo — 蒙特卡洛收益模拟（P5.2）。

目标：基于历史日收益序列，bootstrap 出大量未来路径，统计分布。
- 输入：日收益序列 + 模拟次数 + 路径长度
- 输出：每次模拟的最终收益 / sharpe / max DD / VaR 分布

算法（block bootstrap）：
- 把日收益序列按 block_size 切块（默认 5）
- 每步随机抽一个 block 拼成新路径
- 重复 n_sims 次，统计最终收益 / sharpe / max DD / VaR
- VaR(C) = 收益分布的 (1-C) 分位（如 VaR95 = 5% 分位）

为什么用 block bootstrap（而非 iid shuffle）：
- 金融时序有自相关（波动率聚集），iid 会破坏这种结构
- block bootstrap 保留短程相关性，更接近真实路径
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import asdict, dataclass
from typing import Any

from .backtest_metrics import (
    annualized_return,
    max_drawdown,
    sharpe_ratio,
)

logger = logging.getLogger(__name__)


@dataclass
class MonteCarloSummary:
    n_sims: int
    block_size: int
    horizon: int
    final_return: dict[str, float]  # mean / std / p5 / p50 / p95
    sharpe: dict[str, float]
    max_drawdown: dict[str, float]
    var_95: float
    cvar_95: float  # 95% VaR 条件期望（平均最差 5%）
    prob_profit: float
    raw_returns: list[list[float]]  # 每个模拟的逐日收益

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # raw 可能太大，dict 输出时裁剪到最近 50 个模拟
        d["raw_returns_sample"] = d.pop("raw_returns")[:50]
        d["raw_returns_total"] = self.n_sims
        return d


def _percentile(data: list[float], q: float) -> float:
    """线性插值分位。空返 0。"""
    if not data:
        return 0.0
    s = sorted(data)
    if q <= 0:
        return s[0]
    if q >= 100:
        return s[-1]
    k = (len(s) - 1) * (q / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return s[int(k)]
    return s[f] * (c - k) + s[c] * (k - f)


def _dist_stats(data: list[float]) -> dict[str, float]:
    if not data:
        return {"mean": 0.0, "std": 0.0, "p5": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    mean = sum(data) / len(data)
    var = sum((x - mean) ** 2 for x in data) / len(data)
    return {
        "mean": round(mean, 6),
        "std": round(math.sqrt(var), 6),
        "p5": round(_percentile(data, 5), 6),
        "p50": round(_percentile(data, 50), 6),
        "p95": round(_percentile(data, 95), 6),
        "min": round(min(data), 6),
        "max": round(max(data), 6),
    }


def run_montecarlo(
    returns: list[float],
    n_sims: int = 500,
    horizon: int = 252,
    block_size: int = 5,
    seed: int | None = 42,
) -> dict[str, Any]:
    """Block bootstrap 蒙特卡洛模拟。

    Returns:
        MonteCarloSummary.to_dict() + {"config": {...}, "input_stats": {...}}
    """
    if not returns:
        raise ValueError("returns 序列为空")
    if n_sims < 1:
        raise ValueError(f"n_sims={n_sims} 必须 >= 1")
    if horizon < 1:
        raise ValueError(f"horizon={horizon} 必须 >= 1")
    if block_size < 1:
        raise ValueError(f"block_size={block_size} 必须 >= 1")
    if block_size > len(returns):
        block_size = max(1, len(returns) // 2)

    rng = random.Random(seed)
    n = len(returns)
    # 预切块
    blocks: list[list[float]] = []
    for i in range(0, n - block_size + 1, block_size):
        blocks.append(returns[i : i + block_size])

    final_returns: list[float] = []
    sharpes: list[float] = []
    mdd_list: list[float] = []
    all_paths: list[list[float]] = []

    for _ in range(n_sims):
        path: list[float] = []
        while len(path) < horizon:
            blk = blocks[rng.randrange(len(blocks))]
            path.extend(blk)
        path = path[:horizon]
        all_paths.append(path)
        final_returns.append(annualized_return(path))
        sharpes.append(sharpe_ratio(path))
        mdd_list.append(max_drawdown(path))

    final_returns_sorted = sorted(final_returns)
    var_95 = _percentile(final_returns_sorted, 5)
    # CVaR95 = 平均最差 5%
    cutoff_idx = max(1, int(len(final_returns_sorted) * 0.05))
    cvar_95 = sum(final_returns_sorted[:cutoff_idx]) / cutoff_idx
    prob_profit = sum(1 for r in final_returns if r > 0) / len(final_returns)

    summary = MonteCarloSummary(
        n_sims=n_sims,
        block_size=block_size,
        horizon=horizon,
        final_return=_dist_stats(final_returns),
        sharpe=_dist_stats(sharpes),
        max_drawdown=_dist_stats(mdd_list),
        var_95=round(var_95, 6),
        cvar_95=round(cvar_95, 6),
        prob_profit=round(prob_profit, 4),
        raw_returns=all_paths,
    )
    return {
        "config": {
            "n_sims": n_sims,
            "horizon": horizon,
            "block_size": block_size,
            "seed": seed,
            "input_length": len(returns),
        },
        "input_stats": _dist_stats(returns),
        "summary": summary.to_dict(),
    }
