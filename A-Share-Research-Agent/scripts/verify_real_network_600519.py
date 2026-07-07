"""真实网络端到端验证：sh600519 真实抓取。

跑前：确保可访问外网（akshare / sina / netease 都通）。
跑后：写入 results_real_net_600519.json 备查。
"""
import asyncio
import json
import time
from datetime import date, timedelta
from pathlib import Path

from data_pipeline.cache import InMemoryCache
from data_pipeline.data_router import DataRouter
from data_pipeline.providers.akshare_provider import AkshareProvider
from data_pipeline.providers.netease_provider import NeteaseProvider
from data_pipeline.providers.sina_provider import SinaProvider


def _summarize(r, name: str) -> dict:
    if r is None:
        return {"method": name, "type": "None"}
    if hasattr(r, "shape") and hasattr(r, "columns"):
        sample = r.head(2).to_dict(orient="records")
        return {
            "method": name,
            "type": "DataFrame",
            "shape": list(r.shape),
            "columns": list(r.columns)[:8],
            "sample": sample,
        }
    if isinstance(r, list):
        return {
            "method": name,
            "type": "list",
            "len": len(r),
            "sample": r[:2] if r else [],
        }
    if isinstance(r, dict):
        return {
            "method": name,
            "type": "dict",
            "keys": list(r.keys())[:8],
            "sample": {k: str(v)[:80] for k, v in list(r.items())[:3]},
        }
    return {"method": name, "type": type(r).__name__, "repr": str(r)[:200]}


async def main() -> None:
    print("=" * 60)
    print("真实网络端到端验证：sh600519 / sz000636 / 000001")
    print("=" * 60)
    cache = InMemoryCache()
    router = DataRouter(
        providers={
            "akshare": AkshareProvider(),
            "sina": SinaProvider(),
            "netease": NeteaseProvider(),
        },
        cache=cache,
        cache_ttl=10.0,
    )

    targets = ["sh600519", "sz000636", "000001"]
    results: dict = {}

    # 1) get_tencent_quote (sina/netease)
    for sym in targets:
        t0 = time.time()
        try:
            r = await router.route(
                "get_tencent_quote", [sym], use_cache=True
            )
            elapsed = time.time() - t0
            print(f"  get_tencent_quote({sym}): ok in {elapsed:.2f}s")
            results[f"quote_{sym}"] = _summarize(r, f"get_tencent_quote({sym})")
        except Exception as e:
            print(f"  get_tencent_quote({sym}): ERROR {type(e).__name__}: {e}")
            results[f"quote_{sym}"] = {"error": f"{type(e).__name__}: {e}"}

    # 2) get_daily_bars (akshare 优先；akshare 缺失时 sina fallback 返 list[dict])
    #    sina 只返最近 N 根 K 线，所以日期范围需要落在"今天之前约 30 天内"才有数据
    today = date.today()
    start_recent = (today - timedelta(days=20)).strftime("%Y%m%d")
    end_recent = (today - timedelta(days=5)).strftime("%Y%m%d")
    for sym in ["600519", "000636", "000001"]:
        t0 = time.time()
        try:
            r = await router.route(
                "get_daily_bars",
                sym,
                start_date=start_recent,
                end_date=end_recent,
                use_cache=True,
            )
            elapsed = time.time() - t0
            if hasattr(r, "shape"):
                print(f"  get_daily_bars({sym}): ok in {elapsed:.2f}s, shape={r.shape}")
            elif isinstance(r, list):
                print(f"  get_daily_bars({sym}): ok in {elapsed:.2f}s, len={len(r)} (list[dict] via sina/netease fallback)")
            results[f"daily_{sym}"] = _summarize(r, f"get_daily_bars({sym})")
        except Exception as e:
            print(f"  get_daily_bars({sym}): ERROR {type(e).__name__}: {e}")
            results[f"daily_{sym}"] = {"error": f"{type(e).__name__}: {e}"}

    # 3) get_stock_list
    t0 = time.time()
    try:
        r = await router.route("get_stock_list", use_cache=True)
        elapsed = time.time() - t0
        print(f"  get_stock_list: ok in {elapsed:.2f}s, shape={r.shape}")
        results["stock_list"] = _summarize(r, "get_stock_list")
    except Exception as e:
        print(f"  get_stock_list: ERROR {type(e).__name__}: {e}")
        results["stock_list"] = {"error": f"{type(e).__name__}: {e}"}

    # 健康度
    h = router.health()
    print("=" * 60)
    print(f"端点: {h['endpoints_total']}, 调用 {h['calls_total']} 次, 成功 {h['calls_success']}")
    print(f"成功率: {h['calls_success_rate']}, 缓存命中: {h['cache']['hits']}")
    print("=" * 60)

    # 写文件备查
    out_path = Path("results_real_net_600519.json")
    out_path.write_text(json.dumps({"results": results, "health": h}, ensure_ascii=False, indent=2, default=str))
    print(f"结果写入: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
