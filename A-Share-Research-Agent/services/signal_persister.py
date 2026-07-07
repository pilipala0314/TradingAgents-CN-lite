"""SignalPersister — 把 LangGraph 跑完的 Committee 决策持久化到 `fact_strategy_output`。

4 类信号（每类一条或多条 fact_strategy_output 记录）：
1. **market**   — 顶层市场 verdict（strategy_type="committee_market"）
2. **sector**   — 板块排名（每个 sector 一条，strategy_type="committee_sector"）
3. **approved** — approved_targets 里每个 symbol 一条（strategy_type="committee_approved"）
4. **blocked**  — blocked_targets 里每个 symbol 一条（strategy_type="committee_blocked"）

Upsert 语义：同一 (entity_type, entity_id, business_date, window_type, horizon, strategy_type, output_version)
- 第二次跑：覆盖（direction / confidence / supporting_evidence / output_payload / ... 全更新）
- 适用场景：同一日多次跑 / LLM 重跑 / 修正 verdict

设计原则（与红线对齐）：
- 持久化是 upsert，**不破坏幂等性**（同一 (entity_type, entity_id, business_date, window_type, horizon, strategy_type, output_version) 多次跑结果一致）
- entity_id 用 uuid5(NAMESPACE, f"entity_type:key") 派生，**不依赖 security_master 数据完整**
- LLM 不可用时（fallback_used=true）也落库（带 `fallback_used=true` 标记），保留降级决策可追溯
- 失败语义：DB 写入失败时 raise（不让错误默默吞掉，调度器能感知）
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.outputs import FactDecisionTrace, FactStrategyOutput
from schemas.agent_io import AgentOutputEnvelope

logger = logging.getLogger(__name__)

# ── 命名空间（uuid5 派生用） ────────────────────────────────────────
_ENTITY_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")  # uuid.NAMESPACE_URL
_HORIZON_DEFAULT = "medium"
_OUTPUT_VERSION = 1

# ── 4 类 strategy_type 常量 ─────────────────────────────────────────
STRATEGY_MARKET = "committee_market"
STRATEGY_SECTOR = "committee_sector"
STRATEGY_APPROVED = "committee_approved"
STRATEGY_BLOCKED = "committee_blocked"

ENTITY_TYPE_MARKET = "market"
ENTITY_TYPE_SECTOR = "sector"
ENTITY_TYPE_STOCK = "stock"

# market 顶层用固定 entity_id 派生 key，方便后续取"市场整体"信号
_MARKET_KEY = "market:aggregate"


@dataclass
class SignalPersistResult:
    """落库结果汇总 — 各类别写了几条 / 跳过几条。"""

    market_id: str = ""
    sector_count: int = 0
    approved_count: int = 0
    blocked_count: int = 0
    total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "sector_count": self.sector_count,
            "approved_count": self.approved_count,
            "blocked_count": self.blocked_count,
            "total": self.total,
        }


@dataclass
class DecisionTracePersistResult:
    """决策 trace 落库结果。"""

    trace_id: str = ""
    decision_layer: str = "committee"
    model_version: str = ""
    fallback_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "decision_layer": self.decision_layer,
            "model_version": self.model_version,
            "fallback_used": self.fallback_used,
        }


# LLM 原始响应截断长度（避免单行炸 Postgres Text / 16KB）
_LLM_RAW_MAX_CHARS = 16 * 1024


def _derive_entity_id(entity_type: str, key: str) -> uuid.UUID:
    """从 (entity_type, key) 派生稳定 UUID（uuid5）。"""
    return uuid.uuid5(_ENTITY_NAMESPACE, f"{entity_type}:{key}")


def _coerce_confidence(value: Any) -> float:
    """从各种 confidence 字段归一化到 [0, 1]。"""
    try:
        v = float(value) if value is not None else 0.5
    except (TypeError, ValueError):
        v = 0.5
    return max(0.0, min(1.0, v))


def _coerce_date(d: Any) -> date:
    """兼容 date / datetime / str 输入。"""
    from datetime import datetime as _dt

    if isinstance(d, _dt):
        return d.date()
    if isinstance(d, date):
        return d
    return _dt.fromisoformat(str(d)).date()


# ── 主入口 ────────────────────────────────────────────────────────
async def persist_committee_signals(
    session: AsyncSession,
    *,
    business_date: Any,
    window_type: str,
    market_outlook: AgentOutputEnvelope,
    committee_decision: AgentOutputEnvelope,
    ranked_sectors: list[dict[str, Any]] | None = None,
    stock_outlooks: list[AgentOutputEnvelope] | None = None,
    output_version: int = _OUTPUT_VERSION,
) -> SignalPersistResult:
    """把 4 类信号 upsert 到 fact_strategy_output。

    Args:
        session:            异步 DB session
        business_date:      业务日期（date / datetime / str）
        window_type:        "close" / "intraday" / ...
        market_outlook:     MarketAnalyst 输出（含 market_regime）
        committee_decision: Committee 输出（含 verdict / approved_targets / blocked_targets）
        ranked_sectors:     SectorRankingSynthesizer 输出（list[dict]，含 sector_id / sector_name / sector_score / sector_stage）
        stock_outlooks:     StockAnalyst 输出 list（用于补 approved/blocked 的额外信息：tactical_flags / trend_signal / entry_timing / ...)
        output_version:     输出版本（默认 1）

    Returns:
        SignalPersistResult — 各类别写入条数
    """
    bdate = _coerce_date(business_date)
    stock_outlooks = stock_outlooks or []
    ranked_sectors = ranked_sectors or []

    committee_so = committee_decision.structured_output or {}
    market_so = (market_outlook.structured_output or {}) if market_outlook else {}

    fallback_used = bool(
        "LLM fallback used" in (committee_decision.warnings or [])
    )

    # 构建 stock_outlook 索引（按 symbol 查）
    stock_index: dict[str, AgentOutputEnvelope] = {}
    for env in stock_outlooks:
        so = env.structured_output or {}
        sym = so.get("symbol") or so.get("code") or ""
        if sym:
            stock_index[sym] = env

    # 准备 4 类记录
    rows: list[dict[str, Any]] = []

    # 1) market 顶层
    market_row = _build_market_row(
        bdate=bdate,
        window_type=window_type,
        market_outlook=market_outlook,
        committee_decision=committee_decision,
        market_so=market_so,
        committee_so=committee_so,
        fallback_used=fallback_used,
        output_version=output_version,
    )
    rows.append(market_row)

    # 2) sector ranking（每个 sector 一条）
    for s in ranked_sectors:
        rows.append(
            _build_sector_row(
                bdate=bdate,
                window_type=window_type,
                sector=s,
                market_regime=market_so.get("market_regime", "neutral_mixed"),
                fallback_used=fallback_used,
                output_version=output_version,
            )
        )

    # 3) approved 个股
    approved_targets = committee_so.get("approved_targets") or []
    for tgt in approved_targets:
        sym, conf = _extract_target_meta(tgt)
        if not sym:
            continue
        rows.append(
            _build_stock_row(
                bdate=bdate,
                window_type=window_type,
                symbol=sym,
                strategy_type=STRATEGY_APPROVED,
                direction="long",
                confidence=conf,
                stock_env=stock_index.get(sym),
                market_regime=market_so.get("market_regime", "neutral_mixed"),
                invalid_conditions=[],
                fallback_used=fallback_used,
                output_version=output_version,
            )
        )

    # 4) blocked 个股
    blocked_targets = committee_so.get("blocked_targets") or []
    for tgt in blocked_targets:
        sym, conf, reason = _extract_blocked_meta(tgt)
        if not sym:
            continue
        rows.append(
            _build_stock_row(
                bdate=bdate,
                window_type=window_type,
                symbol=sym,
                strategy_type=STRATEGY_BLOCKED,
                direction="avoid",
                confidence=conf,
                stock_env=stock_index.get(sym),
                market_regime=market_so.get("market_regime", "neutral_mixed"),
                invalid_conditions=[reason] if reason else [],
                fallback_used=fallback_used,
                output_version=output_version,
            )
        )

    # 5) 批量 upsert
    if not rows:
        logger.info("[signal_persister] 4 类信号均为空，跳过落库 business_date=%s", bdate)
        return SignalPersistResult()

    await _upsert_signals(session, rows)

    result = SignalPersistResult(
        market_id=str(rows[0]["entity_id"]) if rows else "",
        sector_count=len(ranked_sectors),
        approved_count=len(approved_targets),
        blocked_count=len(blocked_targets),
        total=len(rows),
    )
    logger.info(
        "[signal_persister] 落库完成 business_date=%s window=%s total=%d (market=1 sector=%d approved=%d blocked=%d, fallback=%s)",
        bdate, window_type, result.total, result.sector_count,
        result.approved_count, result.blocked_count, fallback_used,
    )
    return result


# ── 4 类 row 构造 ──────────────────────────────────────────────────
def _build_market_row(
    *,
    bdate: date,
    window_type: str,
    market_outlook: AgentOutputEnvelope,
    committee_decision: AgentOutputEnvelope,
    market_so: dict[str, Any],
    committee_so: dict[str, Any],
    fallback_used: bool,
    output_version: int,
) -> dict[str, Any]:
    """market 顶层信号 row。"""
    entity_id = _derive_entity_id(ENTITY_TYPE_MARKET, _MARKET_KEY)
    verdict = committee_so.get("verdict") or "neutral"
    confidence = _coerce_confidence(
        committee_decision.confidence
        if committee_decision.confidence is not None
        else committee_so.get("confidence", 0.5)
    )
    risk_budget = committee_so.get("risk_budget") or "normal"
    rationale = committee_so.get("rationale") or committee_decision.narrative_summary or ""

    return {
        "entity_type": ENTITY_TYPE_MARKET,
        "entity_id": entity_id,
        "business_date": bdate,
        "window_type": window_type,
        "horizon": _HORIZON_DEFAULT,
        "direction": _verdict_to_direction(verdict),
        "strategy_type": STRATEGY_MARKET,
        "max_position": None,
        "confidence": confidence,
        "supporting_evidence": [
            {
                "source": "MarketAnalyst",
                "market_regime": market_so.get("market_regime"),
                "risk_mode": market_so.get("risk_mode"),
                "style_rotation_hint": market_so.get("style_rotation_hint"),
                "narrative": market_outlook.narrative_summary if market_outlook else "",
            },
            {
                "source": "CommitteeSynthesizer",
                "verdict": verdict,
                "risk_budget": risk_budget,
                "approved_count": len(committee_so.get("approved_targets") or []),
                "blocked_count": len(committee_so.get("blocked_targets") or []),
            },
        ],
        "invalid_conditions": [],
        "feature_version": 1,
        "rule_version": 1,
        "prompt_version": 1,
        "prompt_bundle_version": 1,
        "model_version": getattr(committee_decision, "model_version", None),
        "output_payload": {
            "verdict": verdict,
            "risk_budget": risk_budget,
            "market_regime": market_so.get("market_regime"),
            "rationale": rationale,
            "fallback_used": fallback_used,
            "warnings": list(committee_decision.warnings or []),
        },
        "output_version": output_version,
    }


def _build_sector_row(
    *,
    bdate: date,
    window_type: str,
    sector: dict[str, Any],
    market_regime: str,
    fallback_used: bool,
    output_version: int,
) -> dict[str, Any]:
    """sector ranking 信号 row。"""
    so = sector.get("structured_output", sector) if isinstance(sector, dict) else {}
    sector_id = so.get("sector_id") or sector.get("sector_id") or "unknown"
    sector_name = so.get("sector_name") or sector.get("sector_name") or sector_id
    score = _coerce_confidence(so.get("sector_score", so.get("score", 0.5)))
    stage = so.get("sector_stage", "watching")
    mainline = bool(so.get("mainline_flag", False))
    direction = "long" if mainline else "neutral"
    entity_id = _derive_entity_id(ENTITY_TYPE_SECTOR, str(sector_id))

    return {
        "entity_type": ENTITY_TYPE_SECTOR,
        "entity_id": entity_id,
        "business_date": bdate,
        "window_type": window_type,
        "horizon": _HORIZON_DEFAULT,
        "direction": direction,
        "strategy_type": STRATEGY_SECTOR,
        "max_position": None,
        "confidence": score,
        "supporting_evidence": [
            {
                "source": "SectorRankingSynthesizer",
                "sector_id": sector_id,
                "sector_name": sector_name,
                "sector_stage": stage,
                "mainline_flag": mainline,
                "stage_evidence": so.get("stage_evidence", ""),
                "market_regime": market_regime,
            }
        ],
        "invalid_conditions": [],
        "feature_version": 1,
        "rule_version": 1,
        "prompt_version": 1,
        "prompt_bundle_version": 1,
        "model_version": None,
        "output_payload": {
            "sector_id": sector_id,
            "sector_name": sector_name,
            "sector_stage": stage,
            "sector_score": score,
            "mainline_flag": mainline,
            "stage_evidence": so.get("stage_evidence", ""),
            "market_regime": market_regime,
            "fallback_used": fallback_used,
        },
        "output_version": output_version,
    }


def _build_stock_row(
    *,
    bdate: date,
    window_type: str,
    symbol: str,
    strategy_type: str,
    direction: str,
    confidence: float,
    stock_env: AgentOutputEnvelope | None,
    market_regime: str,
    invalid_conditions: list[str],
    fallback_used: bool,
    output_version: int,
) -> dict[str, Any]:
    """approved / blocked 个股信号 row。"""
    entity_id = _derive_entity_id(ENTITY_TYPE_STOCK, symbol)
    stock_so = (stock_env.structured_output or {}) if stock_env else {}
    evidence: list[dict[str, Any]] = [
        {
            "source": "CommitteeSynthesizer",
            "symbol": symbol,
            "direction": direction,
            "confidence": confidence,
            "market_regime": market_regime,
        }
    ]
    if stock_env:
        evidence.append(
            {
                "source": "StockAnalyst",
                "symbol": symbol,
                "trend_signal": stock_so.get("trend_signal"),
                "entry_timing": stock_so.get("entry_timing"),
                "risk_mode": stock_so.get("risk_mode"),
                "position_role": stock_so.get("position_role"),
                "conviction_score": stock_so.get("conviction_score"),
                "support_level": stock_so.get("support_level"),
                "resistance_level": stock_so.get("resistance_level"),
                "stop_loss_hint": stock_so.get("stop_loss_hint"),
                "tactical_flags": list(stock_so.get("tactical_flags") or []),
                "upstream_alignment": stock_so.get("upstream_alignment"),
            }
        )

    return {
        "entity_type": ENTITY_TYPE_STOCK,
        "entity_id": entity_id,
        "business_date": bdate,
        "window_type": window_type,
        "horizon": _HORIZON_DEFAULT,
        "direction": direction,
        "strategy_type": strategy_type,
        "max_position": None,
        "confidence": confidence,
        "supporting_evidence": evidence,
        "invalid_conditions": invalid_conditions,
        "feature_version": 1,
        "rule_version": 1,
        "prompt_version": 1,
        "prompt_bundle_version": 1,
        "model_version": None,
        "output_payload": {
            "symbol": symbol,
            "direction": direction,
            "stock_snapshot": stock_so,
            "market_regime": market_regime,
            "fallback_used": fallback_used,
        },
        "output_version": output_version,
    }


# ── helpers ───────────────────────────────────────────────────────
def _verdict_to_direction(verdict: str) -> str:
    """committee verdict → fact_strategy_output.direction 映射。"""
    return {
        "long": "long",
        "short": "short",
        "neutral": "neutral",
        "avoid": "avoid",
    }.get(verdict, "neutral")


def _extract_target_meta(tgt: Any) -> tuple[str, float]:
    """approved_targets 元素可能是 dict {symbol, confidence, ...} 或 str(symbol)。"""
    if isinstance(tgt, str):
        return tgt, 0.6
    if isinstance(tgt, dict):
        sym = tgt.get("symbol") or tgt.get("code") or ""
        conf = _coerce_confidence(tgt.get("confidence", 0.6))
        return str(sym), conf
    return "", 0.5


def _extract_blocked_meta(tgt: Any) -> tuple[str, float, str]:
    """blocked_targets 元素可能是 dict {symbol, reason, ...} 或 str(symbol)。"""
    if isinstance(tgt, str):
        return tgt, 0.7, "blocked"
    if isinstance(tgt, dict):
        sym = tgt.get("symbol") or tgt.get("code") or ""
        conf = _coerce_confidence(tgt.get("confidence", 0.7))
        reason = tgt.get("reason") or "blocked"
        return str(sym), conf, str(reason)
    return "", 0.5, "blocked"


# ── DB 写入（upsert） ─────────────────────────────────────────────
async def _upsert_signals(
    session: AsyncSession,
    rows: list[dict[str, Any]],
) -> None:
    """批量 upsert signals 到 fact_strategy_output。

    upsert 冲突键：UniqueConstraint(entity_type, entity_id, business_date, window_type, horizon, strategy_type, output_version)
    冲突时更新：direction / confidence / supporting_evidence / invalid_conditions / output_payload / max_position
    """
    stmt = pg_insert(FactStrategyOutput).values(rows)
    upsert_cols = {
        "direction": stmt.excluded.direction,
        "confidence": stmt.excluded.confidence,
        "supporting_evidence": stmt.excluded.supporting_evidence,
        "invalid_conditions": stmt.excluded.invalid_conditions,
        "output_payload": stmt.excluded.output_payload,
        "max_position": stmt.excluded.max_position,
        "model_version": stmt.excluded.model_version,
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            "entity_type",
            "entity_id",
            "business_date",
            "window_type",
            "horizon",
            "strategy_type",
            "output_version",
        ],
        set_=upsert_cols,
    )
    await session.execute(stmt)
    await session.commit()


# ── 查询端点辅助（供 routers/research.py 复用） ──────────────────
async def fetch_signals_latest(
    session: AsyncSession,
    *,
    business_date: date | None = None,
    limit_per_type: int = 50,
) -> dict[str, list[dict[str, Any]]]:
    """拉取最新 4 类信号。

    Args:
        session: 异步 session
        business_date: 指定日期（None = 取表中最大 business_date）
        limit_per_type: 每类最多多少条

    Returns:
        {market: [...], sector: [...], approved: [...], blocked: [...]}
    """
    if business_date is None:
        # 取 max business_date
        max_date_stmt = select(FactStrategyOutput.business_date).order_by(
            FactStrategyOutput.business_date.desc()
        ).limit(1)
        result = await session.execute(max_date_stmt)
        business_date = result.scalar_one_or_none()
        if business_date is None:
            return {"market": [], "sector": [], "approved": [], "blocked": []}

    stmt = (
        select(FactStrategyOutput)
        .where(FactStrategyOutput.business_date == business_date)
        .order_by(
            FactStrategyOutput.entity_type,
            FactStrategyOutput.strategy_type,
            FactStrategyOutput.confidence.desc(),
        )
    )
    result = await session.execute(stmt)
    rows = result.scalars().all()

    grouped: dict[str, list[dict[str, Any]]] = {
        "market": [],
        "sector": [],
        "approved": [],
        "blocked": [],
    }
    type_map = {
        STRATEGY_MARKET: "market",
        STRATEGY_SECTOR: "sector",
        STRATEGY_APPROVED: "approved",
        STRATEGY_BLOCKED: "blocked",
    }
    for r in rows:
        key = type_map.get(r.strategy_type)
        if not key:
            continue
        grouped[key].append(_row_to_dict(r))
        # 限流
        if len(grouped[key]) > limit_per_type:
            grouped[key] = grouped[key][:limit_per_type]
    return grouped


def _row_to_dict(r: FactStrategyOutput) -> dict[str, Any]:
    """FactStrategyOutput → dict（供 API 响应）。"""
    return {
        "strategy_id": str(r.strategy_id),
        "entity_type": r.entity_type,
        "entity_id": str(r.entity_id),
        "business_date": r.business_date.isoformat() if r.business_date else "",
        "window_type": r.window_type,
        "horizon": r.horizon,
        "direction": r.direction,
        "strategy_type": r.strategy_type,
        "confidence": float(r.confidence) if r.confidence is not None else 0.0,
        "max_position": float(r.max_position) if r.max_position is not None else None,
        "supporting_evidence": list(r.supporting_evidence or []),
        "invalid_conditions": list(r.invalid_conditions or []),
        "output_payload": dict(r.output_payload or {}),
        "model_version": r.model_version,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }


# ── P4.4 决策 trace 落库 ─────────────────────────────────────
async def persist_decision_trace(
    session: AsyncSession,
    *,
    business_date: Any,
    window_type: str,
    committee_decision: AgentOutputEnvelope,
    market_outlook: AgentOutputEnvelope | None = None,
    decision_layer: str = "committee",
) -> DecisionTracePersistResult:
    """把 Committee 决策 trace upsert 到 `fact_decision_trace`。

    Args:
        session:           异步 DB session
        business_date:     业务日期（date / datetime / str）
        window_type:       "close" / "intraday" / ...
        committee_decision: Committee 返回的 AgentOutputEnvelope，需含 extra_metadata["decision_trace"]
        market_outlook:    MarketAnalyst 输出（可选，用于补 market_regime 字段便于归因）
        decision_layer:    "committee" / 未来 "sector_ranking" / "stock_committee"

    Returns:
        DecisionTracePersistResult — 写入的 trace_id / decision_layer / model_version / fallback_used

    行为：
    - committee_decision.extra_metadata["decision_trace"] 必须存在（由 Committee.run() 写入）
    - 若不存在：记录 warning 并落一个"无 trace"的占位（保留可追溯性）
    - upsert 冲突键：(business_date, window_type, decision_layer, model_version)
    - 同 (date, window, layer, model) 多次跑：覆盖（trace 演化可追溯）
    """
    bdate = _coerce_date(business_date)
    decision_trace = (committee_decision.extra_metadata or {}).get("decision_trace") or {}
    if not decision_trace:
        logger.warning(
            "[decision_trace] committee_decision 缺 extra_metadata.decision_trace，"
            "落占位 trace business_date=%s layer=%s",
            bdate, decision_layer,
        )

    committee_so = committee_decision.structured_output or {}
    market_so = (market_outlook.structured_output or {}) if market_outlook else {}

    verdict = committee_so.get("verdict") or "neutral"
    confidence = _coerce_confidence(
        committee_decision.confidence
        if committee_decision.confidence is not None
        else committee_so.get("confidence", 0.5)
    )
    risk_budget = committee_so.get("risk_budget")
    rationale = committee_so.get("rationale") or committee_decision.narrative_summary or ""
    fallback_used = bool(decision_trace.get("fallback_used")) or bool(
        "LLM fallback used" in (committee_decision.warnings or [])
    )
    llm_error = decision_trace.get("llm_error")
    model_version = decision_trace.get("model_name") or getattr(
        committee_decision, "model_version", None
    )
    market_regime = market_so.get("market_regime") or (
        (decision_trace.get("inputs_snapshot") or {}).get("market_outlook") or {}
    ).get("market_regime")

    llm_response_raw = decision_trace.get("llm_response_raw") or ""
    if llm_response_raw and len(llm_response_raw) > _LLM_RAW_MAX_CHARS:
        llm_response_raw = llm_response_raw[:_LLM_RAW_MAX_CHARS] + "\n...[truncated]"

    row: dict[str, Any] = {
        "business_date": bdate,
        "window_type": window_type,
        "decision_layer": decision_layer,
        "market_regime": market_regime,
        "verdict": verdict,
        "confidence": confidence,
        "risk_budget": risk_budget,
        "fallback_used": fallback_used,
        "llm_error": llm_error,
        "model_version": model_version,
        "rule_baseline": dict(decision_trace.get("rule_baseline") or {}),
        "llm_response_raw": llm_response_raw or None,
        "llm_parsed": dict(decision_trace.get("llm_parsed") or {}),
        "inputs_snapshot": dict(decision_trace.get("inputs_snapshot") or {}),
        "final_decision": dict(decision_trace.get("final_decision") or {}),
        "rationale": rationale,
    }

    await _upsert_decision_trace(session, row)

    result = DecisionTracePersistResult(
        trace_id="",  # upsert 后查询反填
        decision_layer=decision_layer,
        model_version=str(model_version) if model_version else "",
        fallback_used=fallback_used,
    )
    # upsert 后 trace_id 在表中已存在（pk 不变），通过 (date, window, layer, model) 反查 pk
    fetched = await fetch_decision_trace(
        session,
        business_date=bdate,
        window_type=window_type,
        decision_layer=decision_layer,
        model_version=model_version,
    )
    if fetched:
        result.trace_id = fetched["trace_id"]
    logger.info(
        "[decision_trace] 落库完成 business_date=%s window=%s layer=%s "
        "model=%s verdict=%s confidence=%.2f fallback=%s",
        bdate, window_type, decision_layer, model_version or "<none>",
        verdict, confidence, fallback_used,
    )
    return result


async def _upsert_decision_trace(
    session: AsyncSession,
    row: dict[str, Any],
) -> None:
    """upsert 决策 trace 到 fact_decision_trace。

    冲突键：(business_date, window_type, decision_layer, model_version)。
    冲突时更新：除 pk 外的所有字段。
    """
    stmt = pg_insert(FactDecisionTrace).values([row])
    upsert_cols = {
        "market_regime": stmt.excluded.market_regime,
        "verdict": stmt.excluded.verdict,
        "confidence": stmt.excluded.confidence,
        "risk_budget": stmt.excluded.risk_budget,
        "fallback_used": stmt.excluded.fallback_used,
        "llm_error": stmt.excluded.llm_error,
        "rule_baseline": stmt.excluded.rule_baseline,
        "llm_response_raw": stmt.excluded.llm_response_raw,
        "llm_parsed": stmt.excluded.llm_parsed,
        "inputs_snapshot": stmt.excluded.inputs_snapshot,
        "final_decision": stmt.excluded.final_decision,
        "rationale": stmt.excluded.rationale,
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            "business_date",
            "window_type",
            "decision_layer",
            "model_version",
        ],
        set_=upsert_cols,
    )
    await session.execute(stmt)
    await session.commit()


async def fetch_decision_trace(
    session: AsyncSession,
    *,
    business_date: Any,
    window_type: str,
    decision_layer: str = "committee",
    model_version: str | None = None,
) -> dict[str, Any] | None:
    """按 (date, window, layer, model) 拉取单条 trace；找不到返回 None。"""
    bdate = _coerce_date(business_date)
    stmt = select(FactDecisionTrace).where(
        FactDecisionTrace.business_date == bdate,
        FactDecisionTrace.window_type == window_type,
        FactDecisionTrace.decision_layer == decision_layer,
        FactDecisionTrace.model_version == model_version,
    )
    result = await session.execute(stmt)
    r = result.scalar_one_or_none()
    if r is None:
        return None
    return _decision_trace_row_to_dict(r)


async def fetch_decision_traces_by_date(
    session: AsyncSession,
    *,
    business_date: Any,
    decision_layer: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """按日期拉多条 trace（按 created_at 倒序）。

    Args:
        business_date:  业务日期
        decision_layer: 过滤特定决策层（None = 全部）
        limit:          上限
    """
    bdate = _coerce_date(business_date)
    stmt = select(FactDecisionTrace).where(
        FactDecisionTrace.business_date == bdate,
    )
    if decision_layer:
        stmt = stmt.where(FactDecisionTrace.decision_layer == decision_layer)
    stmt = stmt.order_by(FactDecisionTrace.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return [_decision_trace_row_to_dict(r) for r in result.scalars().all()]


def _decision_trace_row_to_dict(r: FactDecisionTrace) -> dict[str, Any]:
    """FactDecisionTrace → dict（供 API / log）。"""
    return {
        "trace_id": str(r.trace_id),
        "business_date": r.business_date.isoformat() if r.business_date else "",
        "window_type": r.window_type,
        "decision_layer": r.decision_layer,
        "market_regime": r.market_regime,
        "verdict": r.verdict,
        "confidence": float(r.confidence) if r.confidence is not None else 0.0,
        "risk_budget": r.risk_budget,
        "fallback_used": bool(r.fallback_used),
        "llm_error": r.llm_error,
        "model_version": r.model_version,
        "rule_baseline": dict(r.rule_baseline or {}),
        "llm_response_raw": r.llm_response_raw,
        "llm_parsed": dict(r.llm_parsed or {}),
        "inputs_snapshot": dict(r.inputs_snapshot or {}),
        "final_decision": dict(r.final_decision or {}),
        "rationale": r.rationale,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }
