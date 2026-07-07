"""Dashboard API — 给前端静态页 (`/static/index.html`) 喂数据。

端点（聚合 DataRouter，失败 fallback 走空 dict 而非 500）：
- `GET /api/dashboard/overview` — 健康度 + 全球指数 + 缓存命中率
- `GET /api/dashboard/stock/{code}` — 单只 A 股行情 + 最近 30 日 K 线 + 最近公告
- `GET /api/dashboard/stock/{code}/decisions` — P0-T10 该股决策钻取
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from data_pipeline.providers.facade import get_router
from services.decision_drilldown_service import fetch_stock_decisions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _normalize_rows(payload: Any) -> list[dict[str, Any]]:
    """把 provider 返回值统一归一化为 list[dict]。

    支持的输入：list[dict] / pandas.DataFrame / None。
    其他类型 / 解析失败 → 记 logger.warning 并返回空 list，
    避免静默让上层以为"数据为空"。
    """
    if isinstance(payload, list):
        return payload
    if payload is None:
        return []
    to_dict = getattr(payload, "to_dict", None)
    if callable(to_dict):
        try:
            rows = to_dict(orient="records")
            if isinstance(rows, list):
                return rows
        except (TypeError, ValueError) as exc:
            logger.warning(
                "DataFrame.to_dict(orient='records') 失败, type=%s err=%s",
                type(payload).__name__, exc,
            )
            return []
        else:
            logger.warning(
                "DataFrame.to_dict 返回非 list, type=%s", type(rows).__name__,
            )
            return []
    logger.warning(
        "无法归一化 provider 返回值, type=%s repr_head=%r",
        type(payload).__name__, str(payload)[:120],
    )
    return []


@router.get("/overview")
async def overview() -> dict[str, Any]:
    """聚合首页数据：health + 全球指数 + cache 命中率。"""
    data_router = get_router()
    health = data_router.health()
    global_quotes: dict[str, Any] = {}
    try:
        global_quotes = await data_router.route(
            "get_global_quote", use_cache=True
        )
    except Exception as exc:
        logger.warning("/api/dashboard/overview get_global_quote 失败: %s", exc)
    return {
        "health": _project_health_summary(health),
        "global_quotes": global_quotes,
    }


@router.get("/stock/{code}")
async def stock_detail(code: str = Path(..., description="6 位股票代码，如 600519")) -> dict[str, Any]:
    """单只 A 股详情：实时行情 + 最近 30 日 K 线 + 最近 14 日公告。"""
    data_router = get_router()
    include_extended = True
    quote: dict[str, Any] = {}
    bars: list[dict[str, Any]] = []
    announcements: list[dict[str, Any]] = []
    errors: dict[str, str] = {}

    try:
        quote = await data_router.route(
            "get_tencent_quote", [code], use_cache=True
        )
    except Exception as exc:
        errors["quote"] = f"{type(exc).__name__}: {exc}"

    try:
        bars = await data_router.route(
            "get_daily_bars", code, datalen=30, use_cache=True
        )  # type: ignore[assignment]
        bars = _normalize_rows(bars)
    except Exception as exc:
        errors["bars"] = f"{type(exc).__name__}: {exc}"

    if include_extended:
        try:
            announcements = await data_router.route(
                "get_cninfo_announcements", code, recent_days=14, use_cache=True
            )  # type: ignore[assignment]
        except Exception as exc:
            errors["announcements"] = f"{type(exc).__name__}: {exc}"

    return {"code": code, "quote": quote, "bars": bars, "announcements": announcements, "errors": errors}


@router.get("/compare")
async def compare_stocks(
    codes: str = Query(..., description="逗号分隔的 6 位代码，最多 5 个"),
) -> dict[str, Any]:
    """多股对比：批量拉各股的最近 N 日 K 线，用于 Dashboard 对比 tab。

    Args:
        codes: 逗号分隔的 6 位股票代码（2-5 个）

    Returns:
        {codes: [...], series: {code: [{date, close, change_pct, volume}, ...]}, errors: {code: msg}}
    """
    raw = [c.strip() for c in codes.split(",") if c.strip()]
    if not raw:
        return {"codes": [], "series": {}, "errors": {"_": "codes 必填"}}
    if len(raw) > 5:
        return {"codes": raw[:5], "series": {}, "errors": {"_": "最多 5 个"}}
    if not all(c.isdigit() and len(c) == 6 for c in raw):
        return {"codes": raw, "series": {}, "errors": {"_": "code 必须 6 位数字"}}

    data_router = get_router()
    series: dict[str, list[dict[str, Any]]] = {}
    errors: dict[str, str] = {}
    for code in raw:
        try:
            bars = await data_router.route(
                "get_daily_bars", code, datalen=60, use_cache=True
            )
            series[code] = _normalize_rows(bars)
        except Exception as exc:
            errors[code] = f"{type(exc).__name__}: {exc}"
            series[code] = []
    return {"codes": raw, "series": series, "errors": errors}


@router.get("/health")
async def health_detail() -> dict[str, Any]:
    """数据源健康监控：每个 endpoint × provider 的 success / failure / 耗时 / 告警。

    前端用此渲染"数据源健康监控"section。
    告警规则（前端 CSS 标红，不阻断响应）：
    - success_rate < 0.5（高失败率）
    - last_error 非空（最近一次失败）
    - p95_latency_ms > 3000（高延迟）
    """
    data_router = get_router()
    h = data_router.health()
    endpoints = h.get("endpoints", [])
    rows: list[dict[str, Any]] = []
    alerts: dict[str, int] = {"low_success_rate": 0, "last_error": 0, "high_latency": 0}
    for e in endpoints:
        total = e.get("total", 0)
        success_rate = e.get("success_rate", 0.0)
        p95 = e.get("p95_latency_ms", 0.0)
        last_error = e.get("last_error", "") or ""
        row_alerts: list[str] = []
        if total > 0 and success_rate < 0.5:
            row_alerts.append("low_success_rate")
            alerts["low_success_rate"] += 1
        if last_error:
            row_alerts.append("last_error")
            alerts["last_error"] += 1
        if p95 > 3000:
            row_alerts.append("high_latency")
            alerts["high_latency"] += 1
        rows.append({
            "method": e.get("method", ""),
            "provider": e.get("provider", ""),
            "total": total,
            "success": e.get("success", 0),
            "failure": e.get("failure", 0),
            "success_rate": success_rate,
            "p95_latency_ms": p95,
            "fallback_count": e.get("fallback_count", 0),
            "fallback_rate": e.get("fallback_rate", 0.0),
            "last_error": last_error,
            "last_status": e.get("last_status", "idle"),
            "last_called_at": e.get("last_called_at", 0.0),
            "request_sample": e.get("request_sample", {}),
            "response_sample": e.get("response_sample", {}),
            "error_sample": e.get("error_sample", {}),
            "alerts": row_alerts,
        })
    response = {
        "providers": h.get("providers", []),
        "endpoints_total": h.get("endpoints_total", 0),
        "calls_total": h.get("calls_total", 0),
        "calls_success": h.get("calls_success", 0),
        "calls_success_rate": h.get("calls_success_rate", 0.0),
        "alerts": alerts,
        "endpoints": rows,
    }
    return response


@router.get("/stock/{code}/decisions")
async def stock_decisions(
    code: str = Path(..., description="6 位股票代码，如 600519"),
    lookback_days: int = Query(30, ge=1, le=180, description="回溯窗口（天）"),
    limit: int = Query(10, ge=1, le=50, description="返回最大条数"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """P0-T10: 该股最近 N 天涉及的委员会决策 trace 钻取。

    返回：
    - security_id / name：从 dim_security 反查
    - traces[]：每条 trace 包含 verdict / role (approved|blocked) / rationale / evidence_refs / fallback_used / llm_error
    """
    try:
        result = await fetch_stock_decisions(
            session, code=code, lookback_days=lookback_days, limit=limit,
        )
    except Exception as exc:  # pragma: no cover
        logger.exception("决策钻取失败 code=%s", code)
        raise HTTPException(status_code=500, detail=f"决策钻取失败: {exc}") from exc
    return result


def _project_health_summary(h: dict[str, Any]) -> dict[str, Any]:
    """取 health 关键字段喂前端，避免一次性吐 50+ 端点详情。"""
    return {
        "providers": h.get("providers", []),
        "endpoints_total": h.get("endpoints_total", 0),
        "calls_total": h.get("calls_total", 0),
        "calls_success_rate": h.get("calls_success_rate", 0.0),
        "cache": h.get("cache", {}),
    }
