"""Dashboard API — 给前端静态页 (`/static/index.html`) 喂数据。

端点（聚合 DataRouter，失败 fallback 走空 dict 而非 500）：
- `GET /api/dashboard/overview` — 健康度 + 全球指数 + 缓存命中率
- `GET /api/dashboard/stock/{code}` — 单只 A 股行情 + 最近 30 日 K 线 + 最近公告
- `GET /api/dashboard/stock/{code}/decisions` — P0-T10 该股决策钻取
- `GET /api/dashboard/sector/heatmap` — P0-T12 申万行业热力图矩阵
"""
from __future__ import annotations

import logging
import uuid
from datetime import date as _date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from data_pipeline.providers.facade import get_router
from data_pipeline.providers.sw_industry_provider import SWIndustryProvider
from services.decision_drilldown_service import fetch_stock_decisions
from services.sector_heatmap_service import build_sector_heatmap

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


# ── P2-T2 资金流端点 ─────────────────────────────


@router.get("/stock/{code}/capital_flow")
async def stock_capital_flow(
    code: str = Path(..., description="6 位股票代码，如 600519"),
    days: int = Query(30, ge=1, le=180, description="回溯窗口（天）"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """P2-T2: 该股最近 N 天资金流（个股 + 市场）数据。

    返回：
    - security_id / name
    - individual: 该股每日 individual_main_force 流（日期 → 净流入/超大单/大单）
    - market: 大盘最近 1 笔 main_force / northbound 净流入
    - summary: KPI 汇总（最新一日净流入、超大单、大单）
    """
    from datetime import datetime, timedelta
    from sqlalchemy import select

    from core.enums import EntityType, FlowType, WindowType
    from models.dimensions import DimSecurity
    from models.facts import FactCapitalFlow

    result: dict[str, Any] = {
        "code": code,
        "security_id": None,
        "name": None,
        "individual": [],
        "market": {},
        "summary": {},
        "errors": {},
    }
    # 1) 反查 security_id
    try:
        sec_q = select(DimSecurity).where(DimSecurity.symbol == code)
        sec_row = (await session.execute(sec_q)).scalar_one_or_none()
        if sec_row:
            result["security_id"] = str(sec_row.security_id)
            result["name"] = sec_row.name
    except Exception as exc:
        result["errors"]["security_lookup"] = f"{type(exc).__name__}: {exc}"

    # 2) 个股资金流（最近 days 天）
    if result["security_id"]:
        try:
            from uuid import UUID
            from sqlalchemy import and_
            cutoff = (datetime.utcnow() - timedelta(days=days)).date()
            q = (
                select(FactCapitalFlow)
                .where(
                    and_(
                        FactCapitalFlow.entity_type == EntityType.SECURITY.value,
                        FactCapitalFlow.entity_id == UUID(result["security_id"]),
                        FactCapitalFlow.business_date >= cutoff,
                    )
                )
                .order_by(FactCapitalFlow.business_date.desc())
            )
            rows = (await session.execute(q)).scalars().all()
            for r in rows:
                result["individual"].append({
                    "date": r.business_date.isoformat() if r.business_date else None,
                    "flow_type": r.flow_type,
                    "net_inflow": float(r.net_inflow) if r.net_inflow is not None else None,
                    "gross_inflow": float(r.gross_inflow) if r.gross_inflow is not None else None,
                    "gross_outflow": float(r.gross_outflow) if r.gross_outflow is not None else None,
                })
        except Exception as exc:
            result["errors"]["individual"] = f"{type(exc).__name__}: {exc}"

    # 3) 大盘资金流（最近 1 笔 main_force / northbound）
    try:
        from core.identifiers import MARKET_ENTITY_ID
        for ft in [FlowType.MAIN_FORCE.value, FlowType.NORTHBOUND.value]:
            q = (
                select(FactCapitalFlow)
                .where(
                    FactCapitalFlow.entity_type == EntityType.MARKET.value,
                    FactCapitalFlow.entity_id == MARKET_ENTITY_ID,
                    FactCapitalFlow.flow_type == ft,
                )
                .order_by(FactCapitalFlow.business_date.desc())
                .limit(1)
            )
            r = (await session.execute(q)).scalars().first()
            if r:
                result["market"][ft] = {
                    "date": r.business_date.isoformat() if r.business_date else None,
                    "net_inflow": float(r.net_inflow) if r.net_inflow is not None else None,
                    "gross_inflow": float(r.gross_inflow) if r.gross_inflow is not None else None,
                }
    except Exception as exc:
        result["errors"]["market"] = f"{type(exc).__name__}: {exc}"

    # 4) 汇总：最新一日的净流入 / 超大单 / 大单
    if result["individual"]:
        latest_ind = [r for r in result["individual"] if r["flow_type"] == "individual_main_force"]
        if latest_ind:
            latest = latest_ind[0]  # 已按日期 desc
            result["summary"] = {
                "date": latest["date"],
                "main_force_net": latest["net_inflow"],
                "super_large_net": latest["gross_inflow"],
                "large_net": latest["gross_outflow"],
            }

    return result


# ── P2-T6 舆情新闻端点 ─────────────────────────────


@router.get("/stock/{code}/news")
async def stock_news(
    code: str = Path(..., description="6 位股票代码"),
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    days: int = Query(7, ge=1, le=90, description="回溯天数"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """P2-T6: 该股最近 N 天的舆情新闻。

    返回：
    - security_id / name
    - news[]: 列表（title / content / url / published_at / sentiment_score / sentiment_label / source_name）
    - aggregate: 分布统计 (positive/neutral/total/avg_score/label)
    """
    from datetime import datetime, timedelta
    from sqlalchemy import and_, select
    from models.dimensions import DimSecurity
    from models.facts import FactNewsItem

    result: dict[str, Any] = {
        "code": code,
        "security_id": None,
        "name": None,
        "news": [],
        "aggregate": {},
        "errors": {},
    }

    # 1) 反查
    try:
        sec_row = (
            await session.execute(select(DimSecurity).where(DimSecurity.symbol == code))
        ).scalar_one_or_none()
        if sec_row:
            result["security_id"] = str(sec_row.security_id)
            result["name"] = sec_row.name
    except Exception as exc:
        result["errors"]["security_lookup"] = f"{type(exc).__name__}: {exc}"

    if not result["security_id"]:
        return result

    # 2) 拉新闻
    try:
        from uuid import UUID
        sec_uuid = UUID(result["security_id"])
        cutoff = datetime.utcnow() - timedelta(days=days)
        q = (
            select(FactNewsItem)
            .where(
                and_(
                    FactNewsItem.security_id == sec_uuid,
                    FactNewsItem.published_at >= cutoff,
                )
            )
            .order_by(FactNewsItem.published_at.desc().nulls_last())
            .limit(limit)
        )
        rows = (await session.execute(q)).scalars().all()
        news_list: list[dict[str, Any]] = []
        scores: list[float] = []
        labels: list[str] = []
        for r in rows:
            news_list.append({
                "news_id": r.news_id,
                "title": r.title,
                "content": r.content,
                "url": r.url,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "source_name": r.source_name,
                "sentiment_score": float(r.sentiment_score) if r.sentiment_score is not None else None,
                "sentiment_label": r.sentiment_label,
            })
            if r.sentiment_score is not None:
                scores.append(float(r.sentiment_score))
            if r.sentiment_label:
                labels.append(r.sentiment_label)
        result["news"] = news_list

        # 3) 聚合
        from data_pipeline.providers.news_sentiment_provider import aggregate_sentiment
        result["aggregate"] = {
            "total_count": len(news_list),
            "positive_count": sum(1 for l in labels if l == "positive"),
            "negative_count": sum(1 for l in labels if l == "negative"),
            "neutral_count": sum(1 for l in labels if l == "neutral"),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "label": (
                "positive" if (sum(scores) / max(1, len(scores))) >= 0.2 else
                "negative" if (sum(scores) / max(1, len(scores))) <= -0.2 else
                "neutral"
            ),
        }
    except Exception as exc:
        result["errors"]["news_query"] = f"{type(exc).__name__}: {exc}"

    return result


# ── P2-T4 财务指标端点 ─────────────────────────────


@router.get("/stock/{code}/financial")
async def stock_financial(
    code: str = Path(..., description="6 位股票代码"),
    periods: int = Query(8, ge=1, le=20, description="返回最近 N 个报告期"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """P2-T4: 该股最近 N 个报告期的关键财务指标。

    返回：
    - security_id / name
    - periods[]: 倒序报告期列表（每期 12 个指标）
    - table: 透视表 (rows=指标, cols=报告期)
    """
    from sqlalchemy import select
    from models.dimensions import DimSecurity
    from models.facts import FactFinancialReport

    result: dict[str, Any] = {
        "code": code,
        "security_id": None,
        "name": None,
        "periods": [],
        "table": [],
        "errors": {},
    }

    # 1) 反查
    try:
        sec_row = (
            await session.execute(select(DimSecurity).where(DimSecurity.symbol == code))
        ).scalar_one_or_none()
        if sec_row:
            result["security_id"] = str(sec_row.security_id)
            result["name"] = sec_row.name
    except Exception as exc:
        result["errors"]["security_lookup"] = f"{type(exc).__name__}: {exc}"

    if not result["security_id"]:
        return result

    # 2) 拉最近 N 期
    try:
        from uuid import UUID
        sec_uuid = UUID(result["security_id"])
        # 拉所有 (security_id, report_period, indicator_code, value)
        q = (
            select(
                FactFinancialReport.report_period,
                FactFinancialReport.indicator_code,
                FactFinancialReport.indicator_name,
                FactFinancialReport.value,
                FactFinancialReport.unit,
            )
            .where(FactFinancialReport.security_id == sec_uuid)
            .order_by(
                FactFinancialReport.report_period.desc(),
                FactFinancialReport.indicator_code.asc(),
            )
        )
        rows = (await session.execute(q)).all()
        # 透视：dict[period] -> dict[code] -> value
        pivot: dict[str, dict[str, dict]] = {}
        for period, code_, name, value, unit in rows:
            ps = period.isoformat() if period else "unknown"
            if ps not in pivot:
                pivot[ps] = {}
            pivot[ps][code_] = {
                "value": float(value) if value is not None else None,
                "name": name,
                "unit": unit,
            }
        # 取最近 N 期
        sorted_periods = sorted(pivot.keys(), reverse=True)[:periods]
        result["periods"] = sorted_periods
        # 透视表
        all_codes: set[str] = set()
        for p in sorted_periods:
            all_codes.update(pivot[p].keys())
        # 固定指标顺序（按 _INDICATOR_MAP 顺序）— 取 dict 中已定义的 key
        from data_pipeline.providers.financial_report_provider import _INDICATOR_MAP
        code_order = [c for c, _ in _INDICATOR_MAP.items() if c in all_codes]
        # 实际：把 indicator_code 排序
        sorted_codes = sorted(all_codes)
        rows_table: list[dict[str, Any]] = []
        for code_ in sorted_codes:
            row: dict[str, Any] = {"indicator": code_}
            for p in sorted_periods:
                cell = pivot[p].get(code_)
                row[p] = cell["value"] if cell else None
            rows_table.append(row)
        result["table"] = rows_table
        # name_map
        result["indicator_names"] = {
            code_: pivot[sorted_periods[0]].get(code_, {}).get("name", code_)
            for code_ in sorted_codes
            if pivot.get(sorted_periods[0], {}).get(code_)
        }
    except Exception as exc:
        result["errors"]["financial_query"] = f"{type(exc).__name__}: {exc}"

    return result


# ── P0-T12 申万行业热力图 ─────────────────────────────



# ── P2-T11/P2-T13 回测 → 线上 signal strategy 衔接 ─────────────────────────────


from services.strategy_selector import StrategySelector, serialize_strategy_config


class SetStrategyRequest(BaseModel):
    run_id: str = Field(..., description="回测 run 的 job_id（UUID）")
    selected_by: str | None = Field(None, description="操作人（前端不传则为 'frontend'）")
    note: str | None = Field(None, description="备注")


class AutoPickRequest(BaseModel):
    days: int = Field(30, ge=1, le=365, description="回看天数")
    metric: str = Field("sharpe", description="排序指标: sharpe / annual_return / total_return")
    selected_by: str | None = Field(None, description="操作人")


@router.get("/signals/current_strategy")
async def get_current_strategy(
    tenant_id: str | None = Query(None),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """返回当前 tenant 激活的 strategy 配置（手动切换 / 自动最优）。"""
    sel = StrategySelector(session)
    cfg = await sel.get_current(tenant_id=tenant_id)
    if not cfg:
        return {"active": False, "config": None}
    return {"active": True, "config": serialize_strategy_config(cfg)}


@router.get("/signals/strategy_history")
async def get_strategy_history(
    tenant_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """返回历史 strategy 配置（按时间倒序）。"""
    sel = StrategySelector(session)
    rows = await sel.list_history(tenant_id=tenant_id, limit=limit)
    return {
        "items": [serialize_strategy_config(c) for c in rows],
        "total": len(rows),
    }


@router.post("/signals/set_strategy")
async def set_strategy(
    req: SetStrategyRequest,
    tenant_id: str | None = Query(None),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """手动切换：根据回测 run_id 写新 active config（关旧 + 开新）。"""
    try:
        run_uuid = uuid.UUID(req.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"run_id 格式错误：{req.run_id}") from exc

    sel = StrategySelector(session)
    try:
        cfg = await sel.set_from_run(
            run_id=run_uuid,
            tenant_id=tenant_id,
            selected_by=req.selected_by or "frontend",
            note=req.note,
            selection_mode="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {
        "ok": True,
        "config": serialize_strategy_config(cfg),
    }


@router.post("/signals/auto_pick_best")
async def auto_pick_best(
    req: AutoPickRequest,
    tenant_id: str | None = Query(None),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """P2-T13 自动从最近 N 天 completed run 中选 metric top 1，切换为 active。

    - 如果没有任何 run → 返回 400（显式报错，禁止静默）
    - 如果有 run 但都是 None metric → 选最早一条并 warn
    """
    sel = StrategySelector(session)
    best_run_id = await sel.pick_best_run(
        tenant_id=tenant_id, days=req.days, metric=req.metric
    )
    if not best_run_id:
        raise HTTPException(
            status_code=400,
            detail=f"最近 {req.days} 天内无 completed run，无法自动选择 strategy",
        )
    try:
        cfg = await sel.set_from_run(
            run_id=best_run_id,
            tenant_id=tenant_id,
            selected_by=req.selected_by or "auto_picker",
            note=f"auto: best {req.metric} over last {req.days}d",
            selection_mode=f"auto_best_{req.metric}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return {
        "ok": True,
        "picked_run_id": str(best_run_id),
        "config": serialize_strategy_config(cfg),
    }




@router.get("/sector/sankey")
async def sector_sankey(
    business_date: _date | None = Query(
        None, description="指定业务日期（None = 最新）"
    ),
    limit_per_type: int = Query(50, ge=1, le=200),
    max_links: int = Query(60, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """P1-T9 板块桑基图 — 行业 → 准入/拦截个股。

    兼容 chartjs-chart-sankey 数据结构：nodes[] + links[]。
    """
    from services.sector_service import get_sector_flow

    try:
        data = await get_sector_flow(
            session,
            business_date=business_date,
            limit_per_type=limit_per_type,
            max_links=max_links,
        )
    except Exception as exc:
        logger.error("sector_sankey 失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    return data


@router.get("/sector/heatmap")
async def sector_heatmap(
    n_days: int = Query(5, ge=1, le=20, description="矩阵列数（交易日数）"),
    lookback_calendar_days: int = Query(30, ge=5, le=180, description="回看日历天数"),
    end_date: str | None = Query(None, description="截止日期 YYYY-MM-DD，默认今天"),
) -> dict[str, Any]:
    """P0-T12: 申万行业指数 31 行 × n_days 列 涨跌幅矩阵。

    返回结构供前端 chartjs-chart-matrix 渲染热力图：
    ```json
    {
      "matrix": [{"code": "801010", "name": "农林牧渔", "values": [pct, ...], "dates": [...]}, ...],
      "dates": ["2026-07-01", ...],   // 全局列
      "industry_count": 31,
      "generated_at": "2026-07-07T..."
    }
    ```
    """
    try:
        provider = SWIndustryProvider()
        result = await build_sector_heatmap(
            provider,
            n_days=n_days,
            lookback_calendar_days=lookback_calendar_days,
            end_date=end_date,
        )
    except Exception as exc:
        logger.exception("sector_heatmap 失败")
        raise HTTPException(status_code=502, detail=f"板块热力图失败: {exc}") from exc
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
