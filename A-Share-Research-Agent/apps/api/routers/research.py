"""Research 端点：完整研究流（DB / Inline）+ 个股分析（Inline / Live）+ 报告 + 信号落库 + 信号查询。

P6 新增：/report 跑完自动 persist committee 决策到 fact_strategy_output；
新增 /signals/{latest,today,by_id} 3 个查询端点供 Dashboard / 外部系统读取。
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agents.stock_agents import StockAnalyst
from apps.api.schemas import InlineResearchRequest, LiveStockRequest, ReportRequest, ResearchRequest
from core.database import get_db
from data_pipeline.providers import stock_live_context
from graph.research_graph import build_research_graph
from graph.state import ResearchWorkflowState
from models.outputs import FactDecisionTrace
from rule_engine.evaluator import RuleEngine
from services.backtest_persister import list_backtest_runs
from services.research_context_loader import ResearchContextLoader
from services.signal_persister import (
    fetch_decision_traces_by_date,
    fetch_signals_latest,
    persist_committee_signals,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/research", tags=["research"])


# ── DB 模式完整研究流 ────────────────────────────────────────────────
@router.post("/run")
async def run_research(
    payload: ResearchRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """从数据库装载快照上下文并触发研究工作流。"""
    try:
        context = await ResearchContextLoader.load(
            session=session,
            business_date=payload.business_date,
            window_type=payload.window_type,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    initial_state: ResearchWorkflowState = {
        "business_date": payload.business_date.isoformat(),
        "window_type": payload.window_type,
        "market_snapshot": context.market_snapshot,
        "market_rule_hits": context.market_rule_hits,
        "sector_snapshots": context.sector_snapshots,
        "sector_rule_hits_map": context.sector_rule_hits_map,
        "market_outlook": None,
        "sector_outlooks": [],
        "ranked_sectors": [],
        "halt_execution": False,
        "halt_reason": "",
    }
    graph = build_research_graph()
    try:
        final = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("research flow failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"ok": True, "result": final}


# ── Inline 模式完整研究流 ────────────────────────────────────────────
@router.post("/run_inline")
async def run_research_inline(payload: InlineResearchRequest) -> dict[str, Any]:
    """端到端研究流调试入口：直接接受内存中的快照与规则命中，跳过数据库。"""
    initial_state: ResearchWorkflowState = {
        "business_date": payload.business_date.isoformat(),
        "window_type": payload.window_type,
        "market_snapshot": payload.market_snapshot,
        "market_rule_hits": payload.market_rule_hits,
        "sector_snapshots": payload.sector_snapshots,
        "sector_rule_hits_map": payload.sector_rule_hits_map,
        "market_outlook": None,
        "sector_outlooks": [],
        "ranked_sectors": [],
        "halt_execution": False,
        "halt_reason": "",
    }
    graph = build_research_graph()
    try:
        final = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("run_inline research flow failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    # 可选：个股层分析
    if payload.target_symbol and payload.stock_snapshot:
        final = await _append_stock_analysis(
            final,
            target_symbol=payload.target_symbol,
            stock_snapshot=payload.stock_snapshot,
            stock_rule_hits=payload.stock_rule_hits,
        )

    return {"ok": True, "result": final}


# ── 个股分析（手工快照） ───────────────────────────────────────────
@router.post("/analyze_stock_inline")
async def analyze_stock_inline(
    payload: InlineResearchRequest,
) -> dict[str, Any]:
    """个股分析独立入口：只针对单只标的做战术判断，跳过板块流。"""
    if not payload.target_symbol or not payload.stock_snapshot:
        raise HTTPException(
            status_code=400,
            detail="target_symbol 与 stock_snapshot 均为必填。",
        )

    market_regime = str(
        payload.stock_snapshot.get("market_regime_hint")
        or payload.market_snapshot.get("market_regime")
        or "neutral_mixed"
    )
    sector_stage = str(payload.stock_snapshot.get("sector_stage_hint") or "watching")

    stock_rule_hits = _evaluate_stock_rules(payload.stock_snapshot, payload.stock_rule_hits)
    envelope = await _run_stock_analyst(
        target_symbol=payload.target_symbol,
        stock_snapshot=payload.stock_snapshot,
        market_regime=market_regime,
        sector_stage=sector_stage,
        stock_rule_hits=stock_rule_hits,
    )

    return {
        "ok": True,
        "result": {
            "target_symbol": payload.target_symbol,
            "business_date": payload.business_date.isoformat(),
            "market_regime": market_regime,
            "sector_stage": sector_stage,
            "stock_rule_hits": stock_rule_hits,
            "stock_outlook": envelope,
        },
    }


# ── 个股分析（实时拉数） ───────────────────────────────────────────
@router.post("/analyze_stock_live")
async def analyze_stock_live(payload: LiveStockRequest) -> dict[str, Any]:
    """个股实景分析：symbol 入参 → 实时拉 a-stock-data 各端点 → 合成 stock_snapshot → 调 StockAnalyst。"""
    if not payload.symbol or not payload.symbol.strip():
        raise HTTPException(status_code=400, detail="symbol 不能为空。")

    business_date = (
        payload.business_date.isoformat() if payload.business_date else None
    )
    logger.info("analyze_stock_live 开始: symbol=%s", payload.symbol)
    try:
        context = await stock_live_context(payload.symbol, business_date)
    except Exception as exc:
        logger.error("stock_live_context 拉取失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"live data fetch failed: {type(exc).__name__}: {exc}",
        ) from exc

    stock_snapshot = context["stock_snapshot"]
    market_regime = context["market_regime"]
    sector_stage = context["sector_stage"]

    stock_rule_hits = _evaluate_stock_rules(stock_snapshot, [])
    envelope = await _run_stock_analyst(
        target_symbol=payload.symbol,
        stock_snapshot=stock_snapshot,
        market_regime=market_regime,
        sector_stage=sector_stage,
        stock_rule_hits=stock_rule_hits,
    )

    return {
        "ok": True,
        "result": {
            "target_symbol": payload.symbol,
            "business_date": (
                context.get("raw", {}).get("kline_tail", [{}])[-1].get("date")
                or business_date
                or ""
            ),
            "market_regime": market_regime,
            "sector_stage": sector_stage,
            "sector_change_pct": context.get("sector_change_pct", 0.0),
            "source_meta": context.get("source_meta", {}),
            "stock_rule_hits": stock_rule_hits,
            "stock_snapshot_preview": {
                k: stock_snapshot.get(k)
                for k in (
                    "symbol",
                    "name",
                    "industry",
                    "current_price",
                    "change_pct",
                    "pe_ttm",
                    "concept_tags",
                    "lhb_listed",
                    "support_level",
                    "resistance_level",
                    "stop_loss_hint",
                )
            },
            "stock_outlook": envelope,
        },
    }


# ── 完整研究流 + 报告渲染 + 信号落库（P4 + P6） ───────────────────
@router.post("/report")
async def render_research_report(
    payload: ReportRequest,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """触发 stock_analyst → committee → report_renderer 三节点，返回 markdown 报告 + 信号落库结果。

    P6 增强：跑完自动把 committee 决策（market / sector / approved / blocked 4 类）
    upsert 到 fact_strategy_output。落库失败不影响 report 渲染（best-effort）。
    """
    initial_state: ResearchWorkflowState = {
        "business_date": payload.business_date.isoformat(),
        "window_type": payload.window_type,
        "target_symbol": "",
        "market_snapshot": payload.market_snapshot,
        "market_rule_hits": payload.market_rule_hits,
        "sector_snapshots": payload.sector_snapshots,
        "sector_rule_hits_map": payload.sector_rule_hits_map,
        "stock_snapshots": payload.stock_snapshots,
        "stock_rule_hits_map": payload.stock_rule_hits_map,
        "market_outlook": None,
        "sector_outlooks": [],
        "stock_outlooks": [],
        "committee_decision": None,
        "final_report": "",
        "ranked_sectors": [],
        "halt_execution": False,
        "halt_reason": "",
    }
    graph = build_research_graph()
    try:
        final = await graph.ainvoke(initial_state)
    except Exception as exc:
        logger.error("research report flow failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    # ── P6: 信号落库（best-effort，不阻断 report 渲染） ──
    persist_result: dict[str, Any] = {"status": "skipped", "reason": "no committee_decision"}
    if final.get("committee_decision") and final.get("market_outlook") is not None:
        try:
            result = await persist_committee_signals(
                session,
                business_date=payload.business_date,
                window_type=payload.window_type,
                market_outlook=final["market_outlook"],
                committee_decision=final["committee_decision"],
                ranked_sectors=final.get("ranked_sectors") or [],
                stock_outlooks=final.get("stock_outlooks") or [],
            )
            persist_result = {"status": "ok", **result.to_dict()}
        except Exception as exc:
            logger.warning(
                "report: persist committee signals 失败，不阻断响应: %s",
                exc,
                exc_info=True,
            )
            persist_result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "report_markdown": final.get("final_report", ""),
        "committee_decision": (
            final["committee_decision"].structured_output
            if final.get("committee_decision") else None
        ),
        "halt": final.get("halt_execution", False),
        "halt_reason": final.get("halt_reason", ""),
        "persist": persist_result,
    }


# ── P6: 信号查询端点 ──────────────────────────────────────────────
@router.get("/signals/latest")
async def get_signals_latest(
    business_date: _date | None = Query(
        None, description="指定业务日期（None = 取表中最新）"
    ),
    limit_per_type: int = Query(50, ge=1, le=200, description="每类最多多少条"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """拉取最新（或指定日期）4 类信号：market / sector / approved / blocked。

    用途：
    - Dashboard "今日信号" tab 直接拉
    - 外部系统做信号消费
    """
    try:
        grouped = await fetch_signals_latest(
            session,
            business_date=business_date,
            limit_per_type=limit_per_type,
        )
    except Exception as exc:
        logger.error("fetch_signals_latest 失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    # 取代表日期
    business_date_iso = ""
    for v in grouped.values():
        if v:
            business_date_iso = v[0].get("business_date", "")
            break
    return {
        "ok": True,
        "business_date": business_date_iso,
        "groups": grouped,
        "summary": {
            "market": len(grouped.get("market", [])),
            "sector": len(grouped.get("sector", [])),
            "approved": len(grouped.get("approved", [])),
            "blocked": len(grouped.get("blocked", [])),
        },
    }


@router.get("/signals/today")
async def get_signals_today(
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """今日信号 — 业务日期为今日（Asia/Shanghai）的 4 类信号。"""
    from datetime import datetime, timedelta, timezone

    today = (datetime.now(timezone(timedelta(hours=8))).date())
    try:
        grouped = await fetch_signals_latest(
            session, business_date=today, limit_per_type=50
        )
    except Exception as exc:
        logger.error("get_signals_today 失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {
        "ok": True,
        "business_date": today.isoformat(),
        "groups": grouped,
        "summary": {
            "market": len(grouped.get("market", [])),
            "sector": len(grouped.get("sector", [])),
            "approved": len(grouped.get("approved", [])),
            "blocked": len(grouped.get("blocked", [])),
        },
    }


@router.get("/signals/report")
async def get_signals_report(
    business_date: _date | None = Query(
        None, description="指定业务日期（None = 最新）"
    ),
    format: str = Query("md", description="导出格式（目前只支持 md）"),
    include_llm: bool = Query(True, description="是否含 LLM AI 解读段落"),
    limit_per_type: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """P1-T7 报告导出 — Markdown。

    复用 fetch_signals_latest 拉 4 类信号，调用 services/report_exporter.export_daily_report
    拼装 Markdown，含 LLM 增强段落（LLM 不可用时显式占位）。
    """
    from services.report_exporter import export_daily_report

    if format != "md":
        raise HTTPException(status_code=400, detail=f"暂不支持格式: {format}")

    try:
        grouped = await fetch_signals_latest(
            session,
            business_date=business_date,
            limit_per_type=limit_per_type,
        )
    except Exception as exc:
        logger.error("get_signals_report fetch_signals_latest 失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    # 业务日期
    business_date_iso = ""
    for v in grouped.values():
        if v:
            business_date_iso = v[0].get("business_date", "")
            break

    md = await export_daily_report(
        business_date=business_date or business_date_iso,
        grouped=grouped,
        attribution=None,
        llm_enhance=include_llm,
    )
    return {
        "ok": True,
        "business_date": business_date_iso,
        "format": format,
        "include_llm": include_llm,
        "content": md,
        "size": len(md),
        "counts": {
            "market": len(grouped.get("market", [])),
            "sector": len(grouped.get("sector", [])),
            "approved": len(grouped.get("approved", [])),
            "blocked": len(grouped.get("blocked", [])),
        },
    }


@router.get("/decision-traces/latest")
async def get_decision_traces_latest(
    business_date: _date | None = Query(
        None, description="指定业务日期（None = 取 trace 表中最新）"
    ),
    decision_layer: str | None = Query(
        None, description='过滤决策层（如 "committee"）'
    ),
    limit: int = Query(10, ge=1, le=100, description="最多返回多少条 trace"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """拉取最新（或指定日期）的决策轨迹，供 Dashboard 做审计追溯。"""
    if business_date is None:
        max_date_stmt = select(FactDecisionTrace.business_date).order_by(
            FactDecisionTrace.business_date.desc()
        ).limit(1)
        result = await session.execute(max_date_stmt)
        business_date = result.scalar_one_or_none()
        if business_date is None:
            return {
                "ok": True,
                "business_date": "",
                "decision_layer": decision_layer or "",
                "summary": _decision_trace_summary([]),
                "traces": [],
            }

    try:
        traces = await fetch_decision_traces_by_date(
            session,
            business_date=business_date,
            decision_layer=decision_layer,
            limit=limit,
        )
    except Exception as exc:
        logger.error("fetch_decision_traces_by_date 失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    return {
        "ok": True,
        "business_date": business_date.isoformat(),
        "decision_layer": decision_layer or "",
        "summary": _decision_trace_summary(traces),
        "traces": traces,
    }


@router.get("/decision-traces/{trace_id}")
async def get_decision_trace_by_id(
    trace_id: str = Path(..., description="decision trace 的 trace_id（uuid）"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """按 trace_id 查看单条决策轨迹详情。"""
    try:
        uuid_obj = UUID(trace_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid uuid: {trace_id}") from exc

    stmt = select(FactDecisionTrace).where(FactDecisionTrace.trace_id == uuid_obj)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"decision trace not found: {trace_id}")

    from services.signal_persister import _decision_trace_row_to_dict

    trace = _decision_trace_row_to_dict(row)
    trace["scientific_checks"] = _decision_trace_checks(trace)
    return {"ok": True, "trace": trace}


@router.get("/audit/latest")
async def get_research_audit_latest(
    business_date: _date | None = Query(
        None, description="指定业务日期（None = 取信号表中的最新）"
    ),
    backtest_limit: int = Query(10, ge=1, le=50, description="关联回测最多返回多少条"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """返回研究审计闭环：decision trace ↔ signal ↔ backtest 的真实一致性核验。"""
    try:
        grouped = await fetch_signals_latest(
            session,
            business_date=business_date,
            limit_per_type=200,
        )
    except Exception as exc:
        logger.error("fetch_signals_latest 失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    resolved_date = business_date
    if resolved_date is None:
        for rows in grouped.values():
            if rows:
                resolved_date = _date.fromisoformat(rows[0].get("business_date", ""))
                break

    if resolved_date is None:
        return {
            "ok": True,
            "business_date": "",
            "consistency": _empty_audit_consistency(),
            "signal_summary": {
                "market": 0,
                "sector": 0,
                "approved": 0,
                "blocked": 0,
            },
            "decision_trace": None,
            "market_signal": None,
            "backtest_runs": [],
        }

    try:
        traces = await fetch_decision_traces_by_date(
            session,
            business_date=resolved_date,
            decision_layer="committee",
            limit=20,
        )
    except Exception as exc:
        logger.error("fetch_decision_traces_by_date 失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    try:
        runs = await list_backtest_runs(
            session,
            business_date=resolved_date,
            limit=backtest_limit,
        )
    except Exception as exc:
        logger.error("list_backtest_runs 失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    decision_trace = traces[0] if traces else None
    market_signal = (grouped.get("market") or [None])[0]
    consistency = _build_audit_consistency(
        decision_trace=decision_trace,
        market_signal=market_signal,
        grouped=grouped,
        backtest_runs=runs,
    )
    return {
        "ok": True,
        "business_date": resolved_date.isoformat(),
        "consistency": consistency,
        "signal_summary": {
            "market": len(grouped.get("market", [])),
            "sector": len(grouped.get("sector", [])),
            "approved": len(grouped.get("approved", [])),
            "blocked": len(grouped.get("blocked", [])),
        },
        "decision_trace": decision_trace,
        "market_signal": market_signal,
        "backtest_runs": runs,
    }


@router.get("/signals/{strategy_id}")
async def get_signal_by_id(
    strategy_id: str = Path(..., description="signal 的 strategy_id（uuid）"),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """按 strategy_id 查单条信号详情（含 supporting_evidence + output_payload）。"""
    from uuid import UUID

    from models.outputs import FactStrategyOutput

    try:
        uuid_obj = UUID(strategy_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid uuid: {strategy_id}") from exc

    stmt = select(FactStrategyOutput).where(FactStrategyOutput.strategy_id == uuid_obj)
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"signal not found: {strategy_id}")

    from services.signal_persister import _row_to_dict

    return {"ok": True, "signal": _row_to_dict(row)}


# ── helpers ─────────────────────────────────────────────────────────
def _decision_trace_checks(trace: dict[str, Any]) -> dict[str, bool]:
    snapshot = trace.get("inputs_snapshot") or {}
    market_ok = bool((snapshot.get("market_outlook") or {}).get("market_regime"))
    sector_ok = bool(snapshot.get("sector_outlooks"))
    stock_ok = bool(snapshot.get("stock_outlooks"))
    llm_parsed = trace.get("llm_parsed") or {}
    rule_baseline = trace.get("rule_baseline") or {}
    final_decision = trace.get("final_decision") or {}
    rationale = (trace.get("rationale") or "").strip()
    llm_error = (trace.get("llm_error") or "").strip()
    return {
        "input_snapshot_complete": market_ok and sector_ok and stock_ok,
        "rule_baseline_present": bool(rule_baseline),
        "llm_parse_success": bool(llm_parsed) and not llm_error,
        "final_decision_present": bool(final_decision),
        "rationale_present": bool(rationale),
    }


def _empty_audit_consistency() -> dict[str, Any]:
    return {
        "trace_present": False,
        "market_signal_present": False,
        "verdict_match": False,
        "confidence_match": False,
        "confidence_delta": None,
        "risk_budget_match": False,
        "market_regime_match": False,
        "fallback_flag_match": False,
        "approved_count_match": False,
        "blocked_count_match": False,
        "backtest_run_count": 0,
        "completed_backtest_count": 0,
        "issues": ["未找到可审计的业务数据"],
    }


def _build_audit_consistency(
    *,
    decision_trace: dict[str, Any] | None,
    market_signal: dict[str, Any] | None,
    grouped: dict[str, list[dict[str, Any]]],
    backtest_runs: list[dict[str, Any]],
) -> dict[str, Any]:
    if not decision_trace and not market_signal:
        return _empty_audit_consistency()

    issues: list[str] = []
    final_decision = (decision_trace or {}).get("final_decision") or {}
    market_payload = (market_signal or {}).get("output_payload") or {}
    trace_verdict = (
        final_decision.get("verdict")
        or (decision_trace or {}).get("verdict")
        or ""
    )
    signal_verdict = market_payload.get("verdict") or (market_signal or {}).get("direction") or ""
    trace_conf = float((decision_trace or {}).get("confidence") or 0.0)
    signal_conf = float((market_signal or {}).get("confidence") or 0.0)
    confidence_delta = abs(trace_conf - signal_conf) if decision_trace and market_signal else None
    approved_count_trace = len(final_decision.get("approved_targets") or [])
    blocked_count_trace = len(final_decision.get("blocked_targets") or [])
    approved_count_signal = len(grouped.get("approved") or [])
    blocked_count_signal = len(grouped.get("blocked") or [])

    verdict_match = bool(decision_trace and market_signal and trace_verdict == signal_verdict)
    confidence_match = bool(confidence_delta is not None and confidence_delta <= 1e-6)
    risk_budget_match = bool(
        decision_trace and market_signal and
        str(final_decision.get("risk_budget") or (decision_trace or {}).get("risk_budget") or "") ==
        str(market_payload.get("risk_budget") or "")
    )
    market_regime_match = bool(
        decision_trace and market_signal and
        str((decision_trace or {}).get("market_regime") or "") ==
        str(market_payload.get("market_regime") or "")
    )
    fallback_flag_match = bool(
        decision_trace and market_signal and
        bool((decision_trace or {}).get("fallback_used")) ==
        bool(market_payload.get("fallback_used"))
    )
    approved_count_match = approved_count_trace == approved_count_signal
    blocked_count_match = blocked_count_trace == blocked_count_signal
    completed_backtest_count = sum(1 for run in backtest_runs if run.get("status") == "completed")

    if not decision_trace:
        issues.append("缺少 committee decision trace")
    if not market_signal:
        issues.append("缺少 market signal")
    if decision_trace and market_signal and not verdict_match:
        issues.append(f"verdict 不一致: trace={trace_verdict} signal={signal_verdict}")
    if decision_trace and market_signal and not confidence_match:
        issues.append(f"confidence 不一致: delta={confidence_delta:.6f}")
    if decision_trace and market_signal and not risk_budget_match:
        issues.append("risk_budget 不一致")
    if decision_trace and market_signal and not market_regime_match:
        issues.append("market_regime 不一致")
    if decision_trace and market_signal and not fallback_flag_match:
        issues.append("fallback 标记不一致")
    if not approved_count_match:
        issues.append(f"approved 数量不一致: trace={approved_count_trace} signal={approved_count_signal}")
    if not blocked_count_match:
        issues.append(f"blocked 数量不一致: trace={blocked_count_trace} signal={blocked_count_signal}")
    if not backtest_runs:
        issues.append("同业务日尚无回测 run")

    return {
        "trace_present": bool(decision_trace),
        "market_signal_present": bool(market_signal),
        "verdict_match": verdict_match,
        "confidence_match": confidence_match,
        "confidence_delta": confidence_delta,
        "risk_budget_match": risk_budget_match,
        "market_regime_match": market_regime_match,
        "fallback_flag_match": fallback_flag_match,
        "approved_count_match": approved_count_match,
        "blocked_count_match": blocked_count_match,
        "backtest_run_count": len(backtest_runs),
        "completed_backtest_count": completed_backtest_count,
        "issues": issues,
    }


def _decision_trace_summary(traces: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(traces)
    if total == 0:
        return {
            "total": 0,
            "fallback_count": 0,
            "fallback_rate": 0.0,
            "llm_error_count": 0,
            "llm_parse_success_rate": 0.0,
            "rationale_coverage_rate": 0.0,
            "input_snapshot_complete_rate": 0.0,
            "rule_baseline_coverage_rate": 0.0,
            "avg_confidence": 0.0,
            "model_versions": [],
            "market_regimes": [],
        }

    fallback_count = sum(1 for t in traces if t.get("fallback_used"))
    llm_error_count = sum(1 for t in traces if (t.get("llm_error") or "").strip())
    rationale_count = sum(1 for t in traces if (t.get("rationale") or "").strip())
    confidence_sum = sum(float(t.get("confidence") or 0.0) for t in traces)
    parse_ok_count = 0
    snapshot_complete_count = 0
    baseline_count = 0
    model_versions: list[str] = []
    market_regimes: list[str] = []
    for trace in traces:
        checks = _decision_trace_checks(trace)
        parse_ok_count += 1 if checks["llm_parse_success"] else 0
        snapshot_complete_count += 1 if checks["input_snapshot_complete"] else 0
        baseline_count += 1 if checks["rule_baseline_present"] else 0
        mv = str(trace.get("model_version") or "").strip()
        regime = str(trace.get("market_regime") or "").strip()
        if mv and mv not in model_versions:
            model_versions.append(mv)
        if regime and regime not in market_regimes:
            market_regimes.append(regime)

    return {
        "total": total,
        "fallback_count": fallback_count,
        "fallback_rate": fallback_count / total,
        "llm_error_count": llm_error_count,
        "llm_parse_success_rate": parse_ok_count / total,
        "rationale_coverage_rate": rationale_count / total,
        "input_snapshot_complete_rate": snapshot_complete_count / total,
        "rule_baseline_coverage_rate": baseline_count / total,
        "avg_confidence": confidence_sum / total,
        "model_versions": model_versions,
        "market_regimes": market_regimes,
    }


def _evaluate_stock_rules(
    stock_snapshot: dict[str, Any],
    fallback: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    try:
        return RuleEngine.evaluate_stock(stock_snapshot)
    except Exception as exc:
        logger.warning("RuleEngine.evaluate_stock 失败: %s", exc)
        return list(fallback or [])


async def _run_stock_analyst(
    *,
    target_symbol: str,
    stock_snapshot: dict[str, Any],
    market_regime: str,
    sector_stage: str,
    stock_rule_hits: list[dict[str, Any]],
):
    analyst = StockAnalyst()
    try:
        return await analyst.run(
            stock_snapshot=stock_snapshot,
            market_regime=market_regime,
            sector_stage=sector_stage,
            rule_hits=stock_rule_hits,
        )
    except Exception as exc:
        logger.error("StockAnalyst 跑失败: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"stock analysis failed: {type(exc).__name__}: {exc}",
        ) from exc


async def _append_stock_analysis(
    final: dict[str, Any],
    *,
    target_symbol: str,
    stock_snapshot: dict[str, Any],
    stock_rule_hits: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    market_regime = (
        (final.get("market_outlook") or {}).get("structured_output", {}).get(
            "market_regime", "neutral_mixed"
        )
    )
    sector_stage = "watching"
    for env in final.get("sector_snapshots") or []:
        sid = (env.get("structured_output") or {}).get("sector_id")
        if sid == stock_snapshot.get("sector_id"):
            sector_stage = (env.get("structured_output") or {}).get(
                "sector_stage", "watching"
            )
            break

    rule_hits = _evaluate_stock_rules(stock_snapshot, stock_rule_hits)
    envelope = await _run_stock_analyst(
        target_symbol=target_symbol,
        stock_snapshot=stock_snapshot,
        market_regime=market_regime,
        sector_stage=sector_stage,
        stock_rule_hits=rule_hits,
    )
    return {
        **final,
        "stock_outlook": envelope,
        "stock_rule_hits": rule_hits,
        "stock_context": {
            "target_symbol": target_symbol,
            "market_regime": market_regime,
            "sector_stage": sector_stage,
        },
    }
