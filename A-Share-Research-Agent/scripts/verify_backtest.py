"""真网路验证脚本：回测引擎端到端（HS300 + 4 策略 + 1 年回测）。

执行流程：
1. 通过 DataRouter 拉沪深 300 成分股 + 拉每只股票日 K 线（real network）
2. 跑 4 个策略的 weekly 调仓回测（top 20, 近 1 年）
3. 写 results_real_net_backtest.json（每策略的 metrics + 调仓次数 + 跳过股票数）
4. 控制台打印关键指标（累计收益 / 年化 / 夏普 / 最大回撤）

跑法：py scripts/verify_backtest.py

注：默认 n_symbols=30 + 半年（~25 分钟）— 全量 300 只 1 年约 2 小时，可手动改 n_symbols/period_days。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import date, timedelta
from pathlib import Path

from data_pipeline.data_router import DataRouter
from data_pipeline.providers.facade import set_router
from services.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    get_hs300_symbols,
)
from services.strategies import list_strategies

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "results_real_net_backtest.json"
N_SYMBOLS_DEFAULT = 30
PERIOD_DAYS_DEFAULT = 180
TOP_N_DEFAULT = 10
REBAL_FREQ_DEFAULT = "weekly"


async def run_one(
    engine: BacktestEngine,
    strategy_name: str,
    symbols: list[str],
    start: date,
    end: date,
    *,
    top_n: int = TOP_N_DEFAULT,
    rebalance_freq: str = REBAL_FREQ_DEFAULT,
) -> dict:
    cfg = BacktestConfig(
        strategy_name=strategy_name,
        symbols=symbols,
        start_date=start,
        end_date=end,
        top_n=top_n,
        rebalance_freq=rebalance_freq,
    )
    t0 = time.time()
    result = engine.run(cfg)
    elapsed = time.time() - t0
    return {
        "strategy_name": strategy_name,
        "elapsed_sec": round(elapsed, 2),
        "n_periods": result.metrics.get("n_periods", 0),
        "n_rebalances": len(result.rebalance_dates),
        "n_trades": len(result.trades),
        "n_skipped": len(result.skipped_symbols),
        "skipped_sample": result.skipped_symbols[:5],
        "metrics": result.metrics,
    }


def main() -> None:
    # 1. 初始化 DataRouter（用 facade 单例）
    router = DataRouter()
    set_router(router)
    engine = BacktestEngine(router=router)

    # 2. 拉 HS300
    print("=" * 60)
    print("Step 1: 拉沪深 300 成分股")
    t0 = time.time()
    symbols = get_hs300_symbols()
    print(f"  → {len(symbols)} 只，耗时 {time.time() - t0:.1f}s")

    if not symbols:
        print("❌ 沪深 300 成分股为空，终止")
        return

    # 3. 回测区间：近 N 天（默认 180 = 半年；用户可改 PERIOD_DAYS_DEFAULT）
    end = date.today()
    start = end - timedelta(days=PERIOD_DAYS_DEFAULT)
    print(f"Step 2: 回测区间 {start} → {end}（近 {PERIOD_DAYS_DEFAULT} 天）")
    symbols_sample = symbols[:N_SYMBOLS_DEFAULT]
    print(f"  股票池：HS300 前 {len(symbols_sample)} 只（要全量改 N_SYMBOLS_DEFAULT）")
    print(f"  调仓频率：{REBAL_FREQ_DEFAULT} / top {TOP_N_DEFAULT}")

    # 4. 跑 4 个策略
    print("Step 3: 跑 4 个策略")
    strategies = [s["name"] for s in list_strategies()]
    results: list[dict] = []
    overall_t0 = time.time()
    for name in strategies:
        print(f"\n--- 策略: {name} ---")
        try:
            r = asyncio.run(
                run_one(
                    engine,
                    name,
                    symbols_sample,
                    start,
                    end,
                    top_n=TOP_N_DEFAULT,
                    rebalance_freq=REBAL_FREQ_DEFAULT,
                )
            )
            m = r["metrics"]
            print(
                f"  调仓 {r['n_rebalances']} 次 / 交易 {r['n_trades']} 次 / 跳过 {r['n_skipped']} 只"
            )
            print(
                f"  累计收益 {m.get('total_return', 0) * 100:+.2f}% / "
                f"年化 {m.get('annualized_return', 0) * 100:+.2f}% / "
                f"夏普 {m.get('sharpe_ratio', 0):.2f} / "
                f"最大回撤 {m.get('max_drawdown', 0) * 100:.2f}%"
            )
            print(f"  耗时 {r['elapsed_sec']}s")
            results.append(r)
        except Exception as exc:
            print(f"  ❌ 失败: {type(exc).__name__}: {exc}")
            results.append({"strategy_name": name, "error": str(exc)})
    overall_elapsed = time.time() - overall_t0

    # 5. 写文件
    payload = {
        "universe": "hs300",
        "n_symbols": len(symbols_sample),
        "symbols_sample": symbols_sample,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "rebalance_freq": REBAL_FREQ_DEFAULT,
        "top_n": TOP_N_DEFAULT,
        "overall_elapsed_sec": round(overall_elapsed, 2),
        "results": results,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + "=" * 60)
    print(f"✅ 全部完成，结果写入 {OUTPUT_PATH.name}")
    print(f"  总耗时 {overall_elapsed:.1f}s")
    print(f"  策略成功: {sum(1 for r in results if 'error' not in r)}/{len(results)}")


if __name__ == "__main__":
    main()
