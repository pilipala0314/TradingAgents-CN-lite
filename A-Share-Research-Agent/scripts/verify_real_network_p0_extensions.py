"""P0 数据源扩展真网路端到端验证。

跑前：确保可访问外网（巨潮 cninfo / 腾讯财经 qt.gtimg.cn 都通）。
跑后：写 results_real_net_p0_extensions.json 备查。
"""
import asyncio
import json
import time
from pathlib import Path

from data_pipeline.cache import InMemoryCache
from data_pipeline.data_router import DataRouter
from data_pipeline.providers.cninfo import CninfoProvider
from data_pipeline.providers.global_quote_provider import GlobalQuoteProvider


def _summarize(r, name: str) -> dict:
    if r is None:
        return {"method": name, "type": "None"}
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
            "sample": {k: {kk: vv for kk, vv in v.items() if kk in ("name", "price", "change_pct")}
                       for k, v in list(r.items())[:3]},
        }
    return {"method": name, "type": type(r).__name__, "repr": str(r)[:200]}


async def main() -> None:
    print("=" * 60)
    print("P0 数据源扩展真网路端到端验证")
    print("=" * 60)
    cache = InMemoryCache()
    router = DataRouter(
        providers={
            "cninfo": CninfoProvider(),
            "global_quote": GlobalQuoteProvider(),
        },
        cache=cache,
        cache_ttl=10.0,
        include_extended=True,
    )

    results: dict = {}

    # 1) 巨潮公告 sh600519
    t0 = time.time()
    try:
        r = await router.route(
            "get_cninfo_announcements", "600519", recent_days=30
        )
        elapsed = time.time() - t0
        print(f"  get_cninfo_announcements(600519): ok in {elapsed:.2f}s, len={len(r)}")
        results["cninfo_600519"] = _summarize(r, "get_cninfo_announcements(600519)")
    except Exception as e:
        print(f"  get_cninfo_announcements(600519): ERROR {type(e).__name__}: {e}")
        results["cninfo_600519"] = {"error": f"{type(e).__name__}: {e}"}

    # 2) 巨潮公告 000001 (平安银行)
    t0 = time.time()
    try:
        r = await router.route(
            "get_cninfo_announcements", "000001", recent_days=30
        )
        elapsed = time.time() - t0
        print(f"  get_cninfo_announcements(000001): ok in {elapsed:.2f}s, len={len(r)}")
        results["cninfo_000001"] = _summarize(r, "get_cninfo_announcements(000001)")
    except Exception as e:
        print(f"  get_cninfo_announcements(000001): ERROR {type(e).__name__}: {e}")
        results["cninfo_000001"] = {"error": f"{type(e).__name__}: {e}"}

    # 3) 国际行情（默认 9 个全球指数）
    t0 = time.time()
    try:
        r = await router.route("get_global_quote")
        elapsed = time.time() - t0
        print(f"  get_global_quote (default 9 indexes): ok in {elapsed:.2f}s, keys={list(r.keys())}")
        results["global_quote_default"] = _summarize(r, "get_global_quote()")
    except Exception as e:
        print(f"  get_global_quote: ERROR {type(e).__name__}: {e}")
        results["global_quote_default"] = {"error": f"{type(e).__name__}: {e}"}

    # 4) 国际行情（港股 hk00700 = 腾讯）
    t0 = time.time()
    try:
        r = await router.route("get_global_quote", codes=["hk00700"])
        elapsed = time.time() - t0
        print(f"  get_global_quote(hk00700): ok in {elapsed:.2f}s, keys={list(r.keys())}")
        results["global_quote_hk00700"] = _summarize(r, "get_global_quote(hk00700)")
    except Exception as e:
        print(f"  get_global_quote(hk00700): ERROR {type(e).__name__}: {e}")
        results["global_quote_hk00700"] = {"error": f"{type(e).__name__}: {e}"}

    h = router.health()
    print("=" * 60)
    print(f"端点: {h['endpoints_total']}, 调用 {h['calls_total']} 次, 成功 {h['calls_success']}")
    print(f"成功率: {h['calls_success_rate']}")
    print("=" * 60)

    out_path = Path("results_real_net_p0_extensions.json")
    out_path.write_text(json.dumps(
        {"results": results, "health": h}, ensure_ascii=False, indent=2, default=str
    ))
    print(f"结果写入: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
