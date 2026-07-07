"""e2e_smoke.py — 端到端冒烟：6 个核心端点 + 关键字段检查。"""
import json
import sys
import time
import urllib.request as u


BASE = "http://127.0.0.1:8012"


def get(path: str, timeout: int = 30) -> tuple[int, dict | str]:
    try:
        r = u.urlopen(f"{BASE}{path}", timeout=timeout)
        body = r.read()
        try:
            return r.status, json.loads(body)
        except json.JSONDecodeError:
            return r.status, body.decode("utf-8", errors="replace")
    except u.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    targets = [
        ("GET /health?mode=summary", "/health?mode=summary"),
        ("GET /health?mode=endpoints", "/health?mode=endpoints"),
        ("GET /api/dashboard/health", "/api/dashboard/health"),
        ("GET /api/dashboard/overview", "/api/dashboard/overview"),
        ("GET /v1/research/signals/latest", "/v1/research/signals/latest"),
        ("GET /v1/research/signals/today", "/v1/research/signals/today"),
        ("GET /v1/research/decision-traces/latest", "/v1/research/decision-traces/latest"),
        ("GET /v1/research/audit/latest", "/v1/research/audit/latest"),
        ("GET /api/dashboard/stock/000636", "/api/dashboard/stock/000636"),
        ("GET /v1/backtest/strategies", "/v1/backtest/strategies"),
        ("GET /v1/backtest/universes", "/v1/backtest/universes"),
        ("GET /v1/backtest/runs", "/v1/backtest/runs"),
        ("GET /v1/macro/snapshot", "/v1/macro/snapshot"),
        ("GET /v1/macro/interpretation", "/v1/macro/interpretation"),
        ("GET /admin/scheduler/status", "/admin/scheduler/status"),
        ("GET /api/health/failover", "/api/health/failover"),
        ("GET /api/health/failover/history?limit=20", "/api/health/failover/history?limit=20"),
        ("GET /api/dashboard/stock/000636/decisions?lookback_days=30&limit=10",
         "/api/dashboard/stock/000636/decisions?lookback_days=30&limit=10"),
        ("GET /api/dashboard/sector/heatmap?n_days=5", "/api/dashboard/sector/heatmap?n_days=5"),
        ("GET /metrics/info", "/metrics/info"),
        ("GET /metrics/traces?limit=3", "/metrics/traces?limit=3"),
        ("GET /admin/tenants", "/admin/tenants"),
        ("GET /admin/alert/rules", "/admin/alert/rules"),
        ("GET /admin/retention/status", "/admin/retention/status"),
    ]
    results = []
    fails = 0
    for label, path in targets:
        t0 = time.time()
        code, body = get(path)
        dt = int((time.time() - t0) * 1000)
        if isinstance(body, dict):
            keys = list(body.keys())[:6]
            summary = f"keys={keys}"
        else:
            summary = f"body_head={str(body)[:120]}"
        results.append({
            "label": label,
            "path": path,
            "code": code,
            "elapsed_ms": dt,
            "summary": summary,
        })
        if code != 200:
            fails += 1
    out = {
        "base": BASE,
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "total": len(targets),
        "failed": fails,
        "results": results,
    }
    Path = __import__("pathlib").Path
    Path("results_e2e_smoke.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
