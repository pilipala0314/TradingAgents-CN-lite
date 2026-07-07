"""Health 端点投影函数单元测试。"""
from __future__ import annotations


def _make_router_health() -> dict:
    """构造一个 mock 的 router.health() 字典。"""
    return {
        "router": "DataRouter",
        "providers": ["akshare", "a_stock_data"],
        "endpoints_total": 3,
        "calls_total": 100,
        "calls_success": 95,
        "calls_success_rate": 0.95,
        "cache": {"hits": 50, "misses": 10, "sets": 60, "deletes": 0, "hit_rate": 0.833},
        "endpoints": [
            {
                "method": "get_a", "provider": "akshare",
                "total": 60, "success": 58, "failure": 2,
                "success_rate": 0.967, "p95_latency_ms": 120.0,
                "fallback_count": 0, "fallback_rate": 0.0,
                "last_error": "", "last_called_at": 0.0,
            },
            {
                "method": "get_b", "provider": "a_stock_data",
                "total": 30, "success": 27, "failure": 3,
                "success_rate": 0.9, "p95_latency_ms": 80.0,
                "fallback_count": 5, "fallback_rate": 0.167,
                "last_error": "timeout", "last_called_at": 0.0,
            },
            {
                "method": "get_a", "provider": "a_stock_data",
                "total": 10, "success": 10, "failure": 0,
                "success_rate": 1.0, "p95_latency_ms": 50.0,
                "fallback_count": 3, "fallback_rate": 0.3,
                "last_error": "", "last_called_at": 0.0,
            },
        ],
    }


def test_project_summary_basic() -> None:
    """summary 模式：极简字段。"""
    from apps.api.routers.health import _project_summary
    h = _project_summary(True, "", _make_router_health())
    assert h["status"] == "ok"
    assert h["db_ok"] is True
    assert h["db_detail"] == ""
    assert h["router"]["endpoints_total"] == 3
    assert h["router"]["cache_hit_rate"] == 0.833
    # provider_health: 各 provider 端点 success_rate 最小值
    assert h["router"]["provider_health"]["akshare"] == 0.967
    assert h["router"]["provider_health"]["a_stock_data"] == 0.9


def test_project_summary_db_degraded() -> None:
    """DB 异常 → status=degraded + 暴露 db_detail。"""
    from apps.api.routers.health import _project_summary
    h = _project_summary(False, "connection refused", _make_router_health())
    assert h["status"] == "degraded"
    assert h["db_ok"] is False
    assert h["db_detail"] == "connection refused"


def test_project_endpoints_by_provider() -> None:
    """endpoints 模式：按 provider 聚合。"""
    from apps.api.routers.health import _project_endpoints
    h = _project_endpoints(_make_router_health())
    assert h["status"] == "ok"
    assert set(h["providers"]) == {"akshare", "a_stock_data"}
    # a_stock_data 应有 2 个端点 (get_b + get_a)
    assert len(h["by_provider"]["a_stock_data"]) == 2
    # akshare 应有 1 个端点 (get_a)
    assert len(h["by_provider"]["akshare"]) == 1


def test_project_full_includes_endpoints() -> None:
    """full 模式：完整 router health 暴露。"""
    from apps.api.routers.health import _project_full
    h = _project_full(True, "", _make_router_health())
    assert "endpoints" in h["router"]
    assert len(h["router"]["endpoints"]) == 3
    assert "cache" in h["router"]


def test_project_summary_handles_empty_router() -> None:
    """router 未初始化时（health() 抛错）summary 不崩。"""
    from apps.api.routers.health import _project_summary
    h = _project_summary(True, "", {"error": "router not initialized"})
    # endpoints_total 默认 0
    assert h["router"]["endpoints_total"] == 0
    assert h["router"]["cache_hit_rate"] == 0.0
    assert h["router"]["provider_health"] == {}
