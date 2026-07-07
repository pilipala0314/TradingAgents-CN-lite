"""DecisionDrilldownService — P0-T10 决策钻取数据服务。

用途：
- 根据股票 code 拉取最近 N 天该股涉及的 FactDecisionTrace
- 提取 verdict / rationale / approved vs blocked 角色 / evidence_refs

设计：
- FactDecisionTrace 不存 symbol，只存 final_decision JSON
  → final_decision.approved_targets / blocked_targets 是 [symbol, ...]
- 反查流程：拉最近 30 天 traces → Python 侧 filter final_decision 含该 code
- 性能：traces/天很少（个位数量级），N+1 可接受
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.dimensions import DimSecurity
from models.outputs import FactDecisionTrace

logger = logging.getLogger(__name__)


async def _resolve_security(
    session: AsyncSession, code: str,
) -> DimSecurity | None:
    """把 6 位 code 解析为 DimSecurity 行。"""
    stmt = select(DimSecurity).where(DimSecurity.symbol == code).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _extract_role(final_decision: dict[str, Any], code: str) -> str | None:
    """判断 code 在 final_decision 中是 approved / blocked / 无关。"""
    approved = final_decision.get("approved_targets") or []
    blocked = final_decision.get("blocked_targets") or []
    if code in approved:
        return "approved"
    if code in blocked:
        return "blocked"
    return None


def _extract_evidence_refs(
    inputs_snapshot: dict[str, Any], code: str,
) -> list[dict[str, Any]]:
    """从 inputs_snapshot 提取该 code 在 stock_outlook 层的 evidence_refs。"""
    if not isinstance(inputs_snapshot, dict):
        return []
    stock = inputs_snapshot.get("stock_outlook")
    if not isinstance(stock, list):
        return []
    for so in stock:
        if isinstance(so, dict) and so.get("symbol") == code:
            ev = so.get("evidence_refs") or so.get("evidence") or []
            return ev if isinstance(ev, list) else []
    return []


def _format_trace(
    trace: FactDecisionTrace, code: str,
) -> dict[str, Any]:
    """把 FactDecisionTrace ORM 序列化为前端可消费的 dict。"""
    final_decision = trace.final_decision or {}
    role = _extract_role(final_decision, code)
    rule_baseline = trace.rule_baseline or {}
    rationale = trace.rationale or final_decision.get("rationale", "")
    return {
        "trace_id": str(trace.trace_id),
        "business_date": trace.business_date.isoformat() if trace.business_date else None,
        "window_type": trace.window_type,
        "verdict": trace.verdict,
        "confidence": float(trace.confidence) if trace.confidence is not None else None,
        "role": role,
        "risk_budget": trace.risk_budget or final_decision.get("risk_budget"),
        "rationale": rationale,
        "fallback_used": trace.fallback_used,
        "model_version": trace.model_version,
        "market_regime": trace.market_regime,
        "llm_error": trace.llm_error,
        "evidence_refs": _extract_evidence_refs(trace.inputs_snapshot or {}, code),
        "approved_targets": final_decision.get("approved_targets", []),
        "blocked_targets": final_decision.get("blocked_targets", []),
        "created_at": trace.created_at.isoformat() if trace.created_at else None,
        "rule_baseline_verdict": rule_baseline.get("verdict"),
        "rule_baseline_rationale": rule_baseline.get("rationale"),
    }


async def fetch_stock_decisions(
    session: AsyncSession,
    *,
    code: str,
    lookback_days: int = 30,
    limit: int = 10,
) -> dict[str, Any]:
    """拉取该 code 涉及的最近 N 条决策 trace。

    Args:
        code: 6 位股票代码
        lookback_days: 回溯窗口
        limit: 返回最大条数

    Returns:
        {
          "code": str,
          "security_id": str|None,
          "name": str|None,
          "traces": [trace dict, ...]
        }
    """
    security = await _resolve_security(session, code)
    security_id = str(security.security_id) if security else None
    security_name = security.name if security else None

    cutoff = (datetime.utcnow() - timedelta(days=lookback_days)).date()
    stmt = (
        select(FactDecisionTrace)
        .where(
            FactDecisionTrace.business_date >= cutoff,
            FactDecisionTrace.decision_layer.in_(["committee", "stock_committee"]),
        )
        .order_by(FactDecisionTrace.business_date.desc(), FactDecisionTrace.created_at.desc())
        .limit(limit * 5)  # 拉多 5 倍供 Python 侧 filter
    )
    result = await session.execute(stmt)
    all_traces = result.scalars().all()

    matched: list[dict[str, Any]] = []
    for tr in all_traces:
        role = _extract_role(tr.final_decision or {}, code)
        if role is None:
            continue
        matched.append(_format_trace(tr, code))
        if len(matched) >= limit:
            break

    return {
        "code": code,
        "security_id": security_id,
        "name": security_name,
        "lookback_days": lookback_days,
        "total_traces_scanned": len(all_traces),
        "matched_count": len(matched),
        "traces": matched,
    }
