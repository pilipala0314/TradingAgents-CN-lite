"""services/walkforward — Walk-forward 滚动窗口验证（P5.2）。

目标：评估策略在样本外（OOS）的稳定性。
- 输入：回测产出的日收益序列（list[float]） + 窗口参数
- 切分：把序列分成 N 个连续窗口，每个窗口 = (train, test) 一对
- 输出：每个 fold 的 OOS 指标 + 汇总

为什么这一层放在 backtest_engine 之外：
- wf 只依赖日收益序列（任何能跑回测的源都能喂入）
- 不耦合 BacktestConfig / BacktestResult 的内部字段
- 测试简单：feed 已知序列即可验证窗口切分正确性

算法（purged walk-forward）：
    [----train----][--test--][----train----][--test--]...
    ^ anchored     ^ rolling
- anchored=True:  train 永远从序列开头到当前位置
- anchored=False: train 长度固定 = window_size * train_pct，滑窗
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from .backtest_metrics import (
    annualized_return,
    max_drawdown,
    sharpe_ratio,
)

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardFold:
    fold_id: int
    train_start: int
    train_end: int  # exclusive
    test_start: int
    test_end: int  # exclusive
    in_sample_metrics: dict[str, float] = field(default_factory=dict)
    out_sample_metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _metrics(returns: list[float]) -> dict[str, float]:
    """给一段收益序列算三个核心指标。"""
    if not returns:
        return {"annual_return": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "n": 0}
    return {
        "annual_return": round(annualized_return(returns), 6),
        "sharpe": round(sharpe_ratio(returns), 4),
        "max_drawdown": round(max_drawdown(returns), 6),
        "n": len(returns),
    }


def split_folds(
    n: int,
    n_folds: int = 5,
    train_pct: float = 0.6,
    anchored: bool = True,
) -> list[tuple[int, int, int, int]]:
    """生成 walk-forward 切分点。

    Returns:
        list of (train_start, train_end, test_start, test_end)
    """
    if n < 10:
        raise ValueError(f"序列太短（n={n}），至少 10 个点")
    if n_folds < 1:
        raise ValueError(f"n_folds={n_folds} 必须 >= 1")
    if not (0 < train_pct < 1):
        raise ValueError(f"train_pct={train_pct} 必须在 (0, 1)")
    folds: list[tuple[int, int, int, int]] = []
    test_size = n // (n_folds + 1) or 1
    for i in range(n_folds):
        test_end = n - i * test_size
        test_start = test_end - test_size
        if test_start <= 0:
            break
        if anchored:
            train_start = 0
            train_end = test_start
        else:
            train_size = int(test_size * train_pct / (1 - train_pct)) or 1
            train_start = max(0, test_start - train_size)
            train_end = test_start
        if train_end - train_start < 2:
            break
        folds.append((train_start, train_end, test_start, test_end))
    return folds


def run_walkforward(
    returns: list[float],
    n_folds: int = 5,
    train_pct: float = 0.6,
    anchored: bool = True,
) -> dict[str, Any]:
    """跑 walk-forward 验证。

    Returns:
        {
          "config": {...},
          "folds": [WalkForwardFold.to_dict()],
          "oos_returns": list[float],   # 拼起来的 OOS 序列
          "oos_metrics": {...},
          "summary": {
            "n_folds": int,
            "mean_oos_sharpe": float,
            "oos_hit_rate": float,  # OOS 收益 > 0 的 fold 占比
            "consistency": float,   # sharpe > 0 的 fold 占比
          },
        }
    """
    folds = split_folds(len(returns), n_folds=n_folds, train_pct=train_pct, anchored=anchored)
    fold_results: list[WalkForwardFold] = []
    oos_returns: list[float] = []
    for i, (ts, te, vs, ve) in enumerate(folds):
        train_seq = returns[ts:te]
        test_seq = returns[vs:ve]
        fold = WalkForwardFold(
            fold_id=i,
            train_start=ts, train_end=te,
            test_start=vs, test_end=ve,
            in_sample_metrics=_metrics(train_seq),
            out_sample_metrics=_metrics(test_seq),
        )
        fold_results.append(fold)
        oos_returns.extend(test_seq)

    n_pos = sum(1 for f in fold_results if f.out_sample_metrics["annual_return"] > 0)
    n_sharpe = sum(1 for f in fold_results if f.out_sample_metrics["sharpe"] > 0)
    oos_sharpe_list = [f.out_sample_metrics["sharpe"] for f in fold_results]
    mean_oos_sharpe = sum(oos_sharpe_list) / len(oos_sharpe_list) if oos_sharpe_list else 0.0

    return {
        "config": {
            "n_folds": n_folds,
            "train_pct": train_pct,
            "anchored": anchored,
            "input_length": len(returns),
        },
        "folds": [f.to_dict() for f in fold_results],
        "oos_returns": oos_returns,
        "oos_metrics": _metrics(oos_returns),
        "summary": {
            "n_folds": len(fold_results),
            "mean_oos_sharpe": round(mean_oos_sharpe, 4),
            "oos_hit_rate": round(n_pos / len(fold_results), 4) if fold_results else 0.0,
            "consistency": round(n_sharpe / len(fold_results), 4) if fold_results else 0.0,
        },
    }
