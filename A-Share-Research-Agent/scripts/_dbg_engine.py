"""debug: 走 BacktestEngine.run 跑 10 只 × 半年，看是否 OK。"""
import time
import traceback
from datetime import date, timedelta

from data_pipeline.data_router import DataRouter
from data_pipeline.providers.facade import set_router
from services.backtest_engine import BacktestConfig, BacktestEngine, get_hs300_symbols


def main() -> None:
    router = DataRouter()
    set_router(router)
    engine = BacktestEngine(router=router)

    symbols = get_hs300_symbols()
    print(f"HS300: {len(symbols)} 只")
    sym10 = symbols[:10]
    end = date.today()
    start = end - timedelta(days=180)
    print(f"区间: {start} -> {end}, {len(sym10)} 只")

    cfg = BacktestConfig(
        strategy_name="momentum_20d",
        symbols=sym10,
        start_date=start,
        end_date=end,
        top_n=5,
        rebalance_freq="weekly",
    )
    try:
        t0 = time.time()
        result = engine.run(cfg)
        print(f"\n成功: 耗时 {time.time() - t0:.1f}s")
        print(f"  调仓 {len(result.rebalance_dates)} / 累计 {result.metrics['total_return'] * 100:+.2f}% / 跳过 {len(result.skipped_symbols)}")
        for s in result.skipped_symbols:
            print(f"    skip: {s}")
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
