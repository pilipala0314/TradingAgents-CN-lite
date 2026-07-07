"""debug: 走 SinaProvider 真接口 + 走 normalize_response + 走 _normalize_rows。"""
import asyncio
import sys
import traceback
from datetime import date, timedelta

from data_pipeline.data_router import DataRouter
from data_pipeline.providers.facade import set_router
from data_pipeline.providers.sina_provider import SinaProvider
from data_pipeline.facade_normalizer import normalize_response
from services.backtest_engine import BacktestEngine


async def main() -> None:
    sp = SinaProvider()
    end = date.today()
    start = end - timedelta(days=180)
    print(f"区间: {start} -> {end}")
    try:
        bars = await sp.get_daily_bars(
            "002625",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
        )
        print(f"SinaProvider 直调: {len(bars)} 根")
        if bars:
            print("前 3 根:", bars[:3])
    except Exception:
        traceback.print_exc()
        return

    # 走 DataRouter
    router = DataRouter()
    set_router(router)
    raw = await router.route(
        "get_daily_bars",
        symbol="002625",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
    )
    print(f"\nDataRouter.route raw type: {type(raw).__name__}")
    if isinstance(raw, list):
        print(f"  list 长度: {len(raw)}")
        if raw:
            print(f"  前 1 根: {raw[0]}")
    else:
        print(f"  值: {str(raw)[:200]}")

    norm = normalize_response("get_daily_bars", raw)
    print(f"\nnormalize_response 后: type={type(norm).__name__}, len={len(norm) if hasattr(norm, '__len__') else 'N/A'}")
    if isinstance(norm, list) and norm:
        print(f"  前 1 根: {norm[0]}")

    # 走 _normalize_rows
    engine = BacktestEngine(router=router)
    normalized = engine._normalize_rows(norm)
    print(f"\n_normalize_rows: {len(normalized)} 根")
    if normalized:
        print(f"  前 1 根: {normalized[0]}")


if __name__ == "__main__":
    asyncio.run(main())
