"""Health 端点。

`/health` 提供 3 个查询模式：
- `mode=summary`（默认）— 极简版，只返 status / db_ok / endpoint 总数 / 缓存命中率。
  适合负载均衡器 / k8s liveness 探针。
- `mode=full`  — 详细版，含所有 method:provider 端点的 success/fallback/p95 详情。
  适合 dashboard / 故障排查。
- `mode=endpoints`— 只返端点列表（不带 DB / cache 总览），按 provider 聚合。
  适合批量健康巡检。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from data_pipeline.providers.facade import get_router
from services.provider_failover import (
    check_and_failover,
    force_recover,
    get_failover_overview,
    get_history,
    get_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def _database_target() -> str:
    return settings.DATABASE_URL.rsplit("@", 1)[-1]


@router.get("/health")
async def health(
    mode: str = Query("summary", pattern="^(summary|full|endpoints)$"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """健康检查端点。

    Args:
        mode:   summary（默认）/ full / endpoints
        session: FastAPI 注入的 DB session

    Returns:
        按 mode 返回对应 schema 的 dict
    """
    # ── DB 自检 ──────────────────────────────────────────────────
    db_ok = True
    db_detail = ""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover
        db_ok = False
        db_detail = str(exc)
        logger.warning("/health 数据库自检失败：%s", db_detail)

    # ── DataRouter 健康度（best-effort） ──────────────────────────
    router_health: dict[str, Any] = {}
    try:
        router_health = get_router().health()
    except Exception as exc:  # pragma: no cover
        router_health = {"error": str(exc)}

    # ── 按 mode 投影 ──────────────────────────────────────────────
    if mode == "summary":
        return _project_summary(db_ok, db_detail, router_health)
    if mode == "endpoints":
        return _project_endpoints(router_health)
    return _project_full(db_ok, db_detail, router_health)


def _project_summary(db_ok: bool, db_detail: str, router_health: dict) -> dict[str, Any]:
    """极简版：status / db_ok / endpoint 数 / cache 命中率。"""
    endpoints_total = router_health.get("endpoints_total", 0)
    calls_total = router_health.get("calls_total", 0)
    cache_stats = router_health.get("cache", {})
    # provider 健康度聚合：各 provider 的 success_rate 最小值
    endpoints = router_health.get("endpoints", [])
    by_provider: dict[str, list[float]] = {}
    for e in endpoints:
        by_provider.setdefault(e["provider"], []).append(e.get("success_rate", 0.0))
    provider_health = {
        p: round(min(rates) if rates else 0.0, 3) for p, rates in by_provider.items()
    }
    return {
        "status": "ok" if db_ok else "degraded",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "db_ok": db_ok,
        "db_target": _database_target(),
        "db_detail": db_detail if not db_ok else "",
        "router": {
            "providers": router_health.get("providers", []),
            "endpoints_total": endpoints_total,
            "calls_total": calls_total,
            "calls_success_rate": router_health.get("calls_success_rate", 0.0),
            "cache_hit_rate": cache_stats.get("hit_rate", 0.0),
            "provider_health": provider_health,
        },
    }


def _project_endpoints(router_health: dict) -> dict[str, Any]:
    """端点视图：按 provider 聚合列表。"""
    endpoints = router_health.get("endpoints", [])
    by_provider: dict[str, list[dict]] = {}
    for e in endpoints:
        by_provider.setdefault(e["provider"], []).append(e)
    return {
        "status": "ok",
        "router": router_health.get("router", "DataRouter"),
        "providers": list(by_provider.keys()),
        "by_provider": by_provider,
    }


def _project_full(db_ok: bool, db_detail: str, router_health: dict) -> dict[str, Any]:
    """详情版：全量信息。"""
    return {
        "status": "ok" if db_ok else "degraded",
        "service": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "db_ok": db_ok,
        "db_target": _database_target(),
        "db_detail": db_detail,
        "router": router_health,
    }


# ── P0-T8/T9 Provider Failover ─────────────────────────────


@router.get("/api/health/failover")
async def failover_overview(
    trigger_check: bool = Query(False, description="是否先扫描一次 stats"),
) -> dict[str, Any]:
    """当前 failover 状态概览（active + all states）。"""
    if trigger_check:
        try:
            check_and_failover(get_router())
        except Exception as exc:
            logger.warning("failover check 失败: %s", exc)
    return get_failover_overview()


@router.get("/api/health/failover/history")
async def failover_history(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Failover 历史变更（最多 200 条，FIFO trim）。"""
    return {"items": get_history(limit=limit), "limit": limit}


@router.get("/api/health/failover/{method}")
async def failover_state(method: str) -> dict[str, Any]:
    """单 method 的 failover 状态。"""
    state = get_state(method)
    if state is None:
        raise HTTPException(status_code=404, detail=f"method={method} 无 failover state")
    return state.to_dict()


@router.post("/api/health/failover/{method}/recover")
async def failover_force_recover(method: str) -> dict[str, Any]:
    """强制退出 failover（运维手动）。"""
    changed = force_recover(method, reason="manual via API")
    return {"method": method, "recovered": changed}
