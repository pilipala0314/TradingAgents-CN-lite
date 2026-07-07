"""P2.2 — 4 策略 × 30 只 HS300 大回测脚本。

目的：
- 真实跑通 4 个内置策略 × 30 只 HS300 成分股（1 年窗口）
- 落 results_4x30.json 供前端对比渲染
- 验证 P2.4 成本 / P2.7 benchmark 链路在真实数据下稳定运行

使用：
    python scripts/run_4x30_backtest.py [--out results_4x30.json]

依赖：
- ak 库（真实数据）
- 数据缓存命中，否则走真实网络

数据选择：
- 从 universe=hs300 池解析（ak.index_stock_cons）随机抽 30 只（固定 seed）
- 时间窗口：today - 365d ~ today
- 调仓频率：weekly
- top_n=10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# 让脚本可直接执行：把项目根加入 path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from data_pipeline.providers.facade import get_router  # noqa: E402
from data_pipeline.universes import clear_cache, resolve_universe  # noqa: E402
from services.backtest_engine import BacktestConfig, BacktestEngine  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_4x30")


# 4 个内置策略名（与 services/strategies.py 对齐）
DEFAULT_STRATEGIES = [
    "momentum_20d",
    "value_pe",
    "low_volatility",
    "reversal_5d",
]

DEFAULT_TOP_N = 10
SAMPLE_N = 30


def _select_universe(router, preset: str, sample_n: int) -> list[str]:
    """从 hs300 universe 抽 30 只（固定 seed=42 便于复现）。"""
    all_codes = asyncio.run(resolve_universe(preset, router=router, use_cache=False))
    if not all_codes:
        raise RuntimeError(f"universe {preset} 解析为空，无法抽样")
    random.seed(42)
    if len(all_codes) > sample_n:
        return sorted(random.sample(all_codes, sample_n))
    return sorted(all_codes)


def _run_one(
    *,
    engine: BacktestEngine,
    strategy_name: str,
    symbols: list[str],
    start: date,
    end: date,
    top_n: int,
    benchmark: str | None,
) -> dict[str, Any]:
    """跑 1 个策略 + 30 只标的的回测，落 result.to_dict() 子集。"""
    config = BacktestConfig(
        strategy_name=strategy_name,
        symbols=symbols,
        universe=None,
        start_date=start,
        end_date=end,
        initial_capital=1_000_000.0,
        top_n=top_n,
        rebalance_freq="weekly",
        commission_rate=0.0003,
        stamp_tax_rate=0.001,
        slippage_rate=0.001,
        benchmark=benchmark,
    )
    logger.info(
        "开始回测：strategy=%s n_symbols=%d [%s ~ %s]",
        strategy_name, len(symbols), start, end,
    )
    t0 = time.time()
    try:
        result = engine.run(config)
        elapsed = time.time() - t0
        m = result.metrics or {}
        out = {
            "strategy_name": strategy_name,
            "n_symbols": len(symbols),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "top_n": top_n,
            "elapsed_sec": round(elapsed, 2),
            "status": "completed",
            # 核心指标
            "total_return_pct": m.get("total_return"),
            "annual_return_pct": m.get("annual_return"),
            "annual_volatility_pct": m.get("annual_volatility"),
            "sharpe_ratio": m.get("sharpe_ratio"),
            "max_drawdown_pct": m.get("max_drawdown"),
            "calmar_ratio": m.get("calmar_ratio"),
            "win_rate_pct": m.get("win_rate"),
            "profit_loss_ratio": m.get("profit_loss_ratio"),
            "n_trades": m.get("n_trades"),
            # 成本 + 基准
            "cost_summary": result.cost_summary,
            "benchmark": result.benchmark,
            # 净值（采样为每周最后一天 + 关键节点；最多 100 点避免 JSON 巨大）
            "nav_curve": _sample_nav(result.returns, max_points=100),
        }
        logger.info(
            "  ✓ %s: 年化 %.2f%%  夏普 %.2f  最大回撤 %.2f%%  成本 %.2f%%  α=%.3f β=%.2f  (耗时 %.1fs)",
            strategy_name,
            m.get("annual_return") or 0,
            m.get("sharpe_ratio") or 0,
            m.get("max_drawdown") or 0,
            (result.cost_summary or {}).get("total_cost_pct", 0) * 100,
            (result.benchmark or {}).get("alpha", 0),
            (result.benchmark or {}).get("beta", 0),
            elapsed,
        )
        return out
    except Exception as exc:
        elapsed = time.time() - t0
        logger.exception("回测失败 strategy=%s: %s", strategy_name, exc)
        return {
            "strategy_name": strategy_name,
            "n_symbols": len(symbols),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "top_n": top_n,
            "elapsed_sec": round(elapsed, 2),
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _sample_nav(
    daily_returns: list[float] | None,
    max_points: int = 100,
) -> list[dict[str, Any]]:
    """从日收益序列算累计净值曲线，采样到 ≤ max_points。"""
    if not daily_returns:
        return []
    navs: list[float] = []
    n = 1.0
    for r in daily_returns:
        n *= (1.0 + (r or 0))
        navs.append(n)
    if len(navs) <= max_points:
        return [{"i": i, "nav": round(v, 4)} for i, v in enumerate(navs)]
    step = max(1, len(navs) // max_points)
    sampled = navs[::step]
    if sampled[-1] != navs[-1]:
        sampled.append(navs[-1])
    return [{"i": i * step, "nav": round(v, 4)} for i, v in enumerate(sampled)]


def main() -> int:
    parser = argparse.ArgumentParser(description="4 策略 × 30 HS300 标的回测")
    parser.add_argument(
        "--out", default="results_4x30.json", help="输出 JSON 路径（默认 results_4x30.json）",
    )
    parser.add_argument(
        "--strategies", nargs="+", default=DEFAULT_STRATEGIES,
        help=f"策略列表（默认：{' '.join(DEFAULT_STRATEGIES)}）",
    )
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="持仓数")
    parser.add_argument(
        "--universe", default="hs300", help="universe preset（默认 hs300）",
    )
    parser.add_argument(
        "--sample-n", type=int, default=SAMPLE_N, help="抽样数（默认 30）",
    )
    parser.add_argument(
        "--benchmark", default="000300.SH", help="基准指数代码（默认 000300.SH；None=不对比）",
    )
    parser.add_argument(
        "--days", type=int, default=365, help="回看天数（默认 365）",
    )
    args = parser.parse_args()

    clear_cache()
    router = get_router()

    end = date.today()
    start = end - timedelta(days=args.days)
    logger.info("回测窗口：%s ~ %s（%d 天）", start, end, args.days)

    # 1) 抽样
    logger.info("解析 universe=%s 抽样 %d 只", args.universe, args.sample_n)
    symbols = _select_universe(router, args.universe, args.sample_n)
    logger.info("抽样结果：%d 只（前 5：%s）", len(symbols), symbols[:5])

    # 2) 跑 4 策略
    engine = BacktestEngine(router=router)
    benchmark = args.benchmark if args.benchmark and args.benchmark.lower() != "none" else None
    results: list[dict[str, Any]] = []
    overall_t0 = time.time()
    for strategy in args.strategies:
        results.append(
            _run_one(
                engine=engine,
                strategy_name=strategy,
                symbols=symbols,
                start=start,
                end=end,
                top_n=args.top_n,
                benchmark=benchmark,
            ),
        )
    overall_elapsed = time.time() - overall_t0

    # 3) 落 JSON
    payload = {
        "meta": {
            "generated_at": date.today().isoformat(),
            "universe": args.universe,
            "n_symbols": len(symbols),
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "top_n": args.top_n,
            "benchmark": benchmark,
            "strategies": args.strategies,
            "elapsed_sec": round(overall_elapsed, 2),
            "n_completed": sum(1 for r in results if r.get("status") == "completed"),
            "n_failed": sum(1 for r in results if r.get("status") == "failed"),
        },
        "symbols": symbols,
        "results": results,
    }
    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info(
        "✓ 完成：%d / %d 成功  总耗时 %.1fs  →  %s",
        payload["meta"]["n_completed"],
        len(args.strategies),
        overall_elapsed,
        out_path.absolute(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
