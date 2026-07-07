"""真网路验证脚本：财务三表端点真拉（600519 + 000001）"""
import asyncio
import json
import time
from pathlib import Path

from data_pipeline.data_router import DataRouter
from data_pipeline.facade_normalizer import normalize_response
from data_pipeline.providers.facade import set_router


async def main() -> None:
    r = DataRouter(include_extended=True)
    set_router(r)
    result: dict = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}

    summary: dict[str, dict] = {}
    for code in ["600519", "000001"]:
        print(f"\n=== code={code} ===", flush=True)
        for m in ["get_balance_sheet", "get_cashflow_statement", "get_income_statement"]:
            t0 = time.time()
            try:
                v = await r.route(m, code, use_cache=False, timeout=30)
                # 显式归一化（route 内部已经归一化，但保险起见再 normalize 一次）
                v_norm = normalize_response(m, v)
                elapsed = time.time() - t0
                cnt = len(v_norm) if isinstance(v_norm, list) else 0
                sample = v_norm[0] if cnt > 0 else None
                print(
                    f"  {m}: type={type(v_norm).__name__}, count={cnt}, "
                    f"elapsed={elapsed:.2f}s",
                    flush=True,
                )
                summary[f"{code}:{m}"] = {
                    "ok": cnt > 0,
                    "count": cnt,
                    "elapsed_sec": round(elapsed, 3),
                    "sample_keys": list(sample.keys())[:6] if sample else [],
                }
            except Exception as e:
                elapsed = time.time() - t0
                print(f"  {m}: ERROR {type(e).__name__}: {e}", flush=True)
                summary[f"{code}:{m}"] = {
                    "ok": False,
                    "elapsed_sec": round(elapsed, 3),
                    "error": f"{type(e).__name__}: {e}",
                }
    result["summary"] = summary
    out = Path("results_real_net_financial_reports.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n结果写入 {out}", flush=True)
    ok = sum(1 for v in summary.values() if v["ok"])
    print(f"ok={ok}/{len(summary)}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
