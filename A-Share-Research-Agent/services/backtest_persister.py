"""backtest_persister — P2.5 / P2.3 回测 run 落库。

- persist_backtest_run：upsert（按 config_hash + business_date 冲突键）
- update_backtest_run_status：状态机推进（pending → running → completed/failed）
- fetch_backtest_run：按 job_id 查
- list_backtest_runs：按日期/状态/策略列表

status 状态机（防止越级）：
- pending    → running / cancelled
- running    → completed / failed
- completed  /  failed  /  cancelled  → 终态，不允许再改
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.outputs import FactBacktestRun
from services.backtest_engine import BacktestConfig, BacktestResult

logger = logging.getLogger(__name__)


# 终态：completed / failed / cancelled
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_VALID_TRANSITIONS = {
    "pending": {"running", "cancelled", "failed"},
    "running": {"completed", "failed"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}


@dataclass
class BacktestRunRecord:
    """回测 run 落库结果。"""

    run_id: str
    job_id: str
    status: str
    config_hash: str
    business_date: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "job_id": self.job_id,
            "status": self.status,
            "config_hash": self.config_hash,
            "business_date": self.business_date,
        }


def compute_config_hash(config: BacktestConfig) -> str:
    """BacktestConfig → SHA256 前 16 位 hex（用于同配置去重）。

    注意：传入 dataclass 的字段（__dict__）而不是 instance id 之类不可靠引用。
    """
    d = {
        "strategy_name": config.strategy_name,
        "symbols": sorted(config.symbols),
        "universe": config.universe,
        "start_date": config.start_date.isoformat(),
        "end_date": config.end_date.isoformat(),
        "top_n": config.top_n,
        "rebalance_freq": config.rebalance_freq,
        "commission_rate": config.commission_rate,
        "stamp_tax_rate": config.stamp_tax_rate,
        "slippage_rate": config.slippage_rate,
        "benchmark": config.benchmark,
    }
    raw = json.dumps(d, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def config_to_dict(config: BacktestConfig) -> dict[str, Any]:
    """BacktestConfig → JSON-safe dict（DB 落库用）。"""
    return {
        "strategy_name": config.strategy_name,
        "symbols": list(config.symbols),
        "universe": config.universe,
        "start_date": config.start_date.isoformat(),
        "end_date": config.end_date.isoformat(),
        "initial_capital": config.initial_capital,
        "top_n": config.top_n,
        "rebalance_freq": config.rebalance_freq,
        "commission_rate": config.commission_rate,
        "stamp_tax_rate": config.stamp_tax_rate,
        "slippage_rate": config.slippage_rate,
        "benchmark": config.benchmark,
    }


# ── upsert & update ─────────────────────────────────────────
async def persist_backtest_run(
    session: AsyncSession,
    *,
    config: BacktestConfig,
    business_date: Any,
    job_id: str | None = None,
    status: str = "pending",
) -> BacktestRunRecord:
    """落 FactBacktestRun 行（upsert by (config_hash, business_date)）。

    Args:
        session:        异步 DB session
        config:         BacktestConfig（hash 用）
        business_date:  业务日期（date / datetime / str）
        job_id:         异步 job ID（UUID str，None=同步）
        status:         初始状态（默认 pending）

    Returns:
        BacktestRunRecord（run_id, job_id, status, config_hash, business_date）
    """
    bdate = _coerce_date(business_date)
    cfg_hash = compute_config_hash(config)
    cfg_json = config_to_dict(config)

    row: dict[str, Any] = {
        "job_id": job_id,
        "business_date": bdate,
        "status": status,
        "config_hash": cfg_hash,
        "config_json": cfg_json,
        "result_json": None,
        "error_message": None,
    }
    stmt = pg_insert(FactBacktestRun).values([row])
    # upsert 冲突键：(config_hash, business_date) — 覆盖 status / job_id / config_json
    update_cols = {
        "status": stmt.excluded.status,
        "job_id": stmt.excluded.job_id,
        "config_json": stmt.excluded.config_json,
    }
    stmt = stmt.on_conflict_do_update(
        index_elements=["config_hash", "business_date"],
        set_=update_cols,
    )
    await session.execute(stmt)
    await session.commit()
    # 查回 run_id
    fetched = await fetch_by_hash_and_date(
        session, config_hash=cfg_hash, business_date=bdate,
    )
    return BacktestRunRecord(
        run_id=fetched["run_id"] if fetched else "",
        job_id=job_id or "",
        status=status,
        config_hash=cfg_hash,
        business_date=bdate.isoformat(),
    )


async def update_backtest_run_status(
    session: AsyncSession,
    *,
    job_id: str,
    status: str,
    result: BacktestResult | None = None,
    error_message: str | None = None,
    attribution: dict[str, Any] | None = None,
    allowed_from: set[str] | None = None,
) -> bool:
    """推进 run 状态。

    Args:
        session:        DB session
        job_id:         异步 job ID
        status:         新状态
        result:         BacktestResult（completed 时填，自动提取 cost / benchmark）
        error_message:  失败原因
        attribution:    P4.5 归因结果（落 attribution_json）
        allowed_from:   允许的前置状态集合（None=用默认状态机）

    Returns:
        True=更新成功，False=状态机不合法（不抛错）
    """
    if status not in _VALID_TRANSITIONS:
        logger.warning("未知状态：%s", status)
        return False
    # 找当前 run
    stmt = select(FactBacktestRun).where(FactBacktestRun.job_id == job_id)
    res = await session.execute(stmt)
    run = res.scalar_one_or_none()
    if run is None:
        logger.warning("[backtest_run] job_id=%s 不存在，无法更新状态", job_id)
        return False
    # 状态机校验
    allowed = allowed_from or _VALID_TRANSITIONS.get(run.status, set())
    if status not in allowed:
        logger.warning(
            "[backtest_run] job_id=%s 状态 %s → %s 非法（允许：%s）",
            job_id, run.status, status, allowed,
        )
        return False
    # 终态不动
    if run.status in _TERMINAL_STATUSES:
        logger.warning(
            "[backtest_run] job_id=%s 终态 %s 不可再改 → %s",
            job_id, run.status, status,
        )
        return False
    # 构造更新
    from datetime import datetime as _dt
    update_payload: dict[str, Any] = {"status": status}
    if status == "running":
        update_payload["started_at"] = _dt.utcnow()
    elif status == "completed":
        update_payload["finished_at"] = _dt.utcnow()
        if result is not None:
            update_payload["result_json"] = result.to_dict()
            update_payload["cost_total_pct"] = float(
                result.cost_summary.get("total_cost_pct", 0) if result.cost_summary else 0
            )
            if result.benchmark:
                update_payload["benchmark_alpha"] = result.benchmark.get("alpha")
                update_payload["benchmark_beta"] = result.benchmark.get("beta")
    elif status == "failed":
        update_payload["finished_at"] = _dt.utcnow()
        update_payload["error_message"] = error_message
    if attribution is not None:
        update_payload["attribution_json"] = attribution
    # 执行更新
    from sqlalchemy import update as sa_update
    stmt = sa_update(FactBacktestRun).where(
        FactBacktestRun.job_id == job_id,
        FactBacktestRun.status.notin_(_TERMINAL_STATUSES),
    ).values(**update_payload)
    res = await session.execute(stmt)
    await session.commit()
    if res.rowcount == 0:
        # 已被并发推进 — 不报错（让上层决定）
        logger.info("[backtest_run] job_id=%s 状态已被其他进程推进", job_id)
    return True


# ── fetch helpers ─────────────────────────────────────────
async def fetch_by_job_id(
    session: AsyncSession, job_id: str,
) -> dict[str, Any] | None:
    """按 job_id 查 run。"""
    stmt = select(FactBacktestRun).where(FactBacktestRun.job_id == job_id)
    res = await session.execute(stmt)
    run = res.scalar_one_or_none()
    return _row_to_dict(run) if run else None


async def fetch_by_hash_and_date(
    session: AsyncSession,
    *,
    config_hash: str,
    business_date: Any,
) -> dict[str, Any] | None:
    """按 (config_hash, business_date) 查 run。"""
    bdate = _coerce_date(business_date)
    stmt = select(FactBacktestRun).where(
        FactBacktestRun.config_hash == config_hash,
        FactBacktestRun.business_date == bdate,
    )
    res = await session.execute(stmt)
    run = res.scalar_one_or_none()
    return _row_to_dict(run) if run else None


async def list_backtest_runs(
    session: AsyncSession,
    *,
    business_date: Any | None = None,
    status: str | None = None,
    strategy_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """历史 run 列表（按 created_at 倒序）。

    Args:
        business_date:  过滤特定日期
        status:         过滤特定状态
        strategy_name:  过滤特定策略（从 config_json 拆）
        limit:          上限
    """
    stmt = select(FactBacktestRun).order_by(FactBacktestRun.created_at.desc())
    if business_date is not None:
        stmt = stmt.where(FactBacktestRun.business_date == _coerce_date(business_date))
    if status:
        stmt = stmt.where(FactBacktestRun.status == status)
    # strategy_name 过滤需要 JSONB 提取：config_json ->> 'strategy_name' = X
    if strategy_name:
        stmt = stmt.where(
            FactBacktestRun.config_json["strategy_name"].astext == strategy_name,
        )
    stmt = stmt.limit(limit)
    res = await session.execute(stmt)
    return [_row_to_dict(r) for r in res.scalars().all()]


# ── P1-T3 多 run 横向对比 ─────────────────────────────


async def compare_runs(
    session: AsyncSession,
    job_ids: list[str],
    *,
    max_n: int = 5,
) -> list[dict[str, Any]]:
    """按 job_ids 拉多 run 横向对比（最多 max_n 个）。

    返回每 run 的关键指标 + nav 曲线（对齐到日期轴）。
    缺失的 job_id 跳过 + 记录到 missing。
    """
    if not job_ids:
        return []
    # 截断 + 去重
    seen: set[str] = set()
    target: list[str] = []
    for jid in job_ids:
        if jid and jid not in seen:
            seen.add(jid)
            target.append(jid)
        if len(target) >= max_n:
            break

    # 用 IN 一次拉
    from sqlalchemy import cast
    from sqlalchemy.dialects.postgresql import UUID as PG_UUID

    stmt = select(FactBacktestRun).where(
        cast(FactBacktestRun.job_id, PG_UUID).in_([_to_uuid(jid) for jid in target])
    )
    res = await session.execute(stmt)
    rows = {str(r.job_id): r for r in res.scalars().all()}

    out: list[dict[str, Any]] = []
    for jid in target:
        row = rows.get(jid)
        if row is None:
            logger.warning("[backtest_run] compare_runs: job_id=%s 未找到", jid)
            continue
        result = dict(row.result_json or {}) if row.result_json else {}
        config = dict(row.config_json or {})
        metrics = dict(result.get("metrics") or {})
        bench = result.get("benchmark") or {}
        nav_list = result.get("nav") or []
        dates_list = result.get("trading_dates") or []
        # nav 曲线：date+nav 对齐
        nav_curve_pts: list[dict[str, Any]] = []
        for i, v in enumerate(nav_list):
            d = dates_list[i] if i < len(dates_list) else None
            nav_curve_pts.append({"date": d, "nav": v})
        out.append({
            "run_id": str(row.id),
            "job_id": jid,
            "strategy": config.get("strategy_name"),
            "universe": config.get("universe"),
            "benchmark": config.get("benchmark"),
            "status": row.status,
            "business_date": row.business_date.isoformat() if row.business_date else None,
            "alpha": (float(row.benchmark_alpha)
                      if row.benchmark_alpha is not None else bench.get("alpha")),
            "beta": (float(row.benchmark_beta)
                     if row.benchmark_beta is not None else bench.get("beta")),
            "sharpe": metrics.get("sharpe"),
            "turnover": metrics.get("turnover") or metrics.get("avg_turnover"),
            "max_drawdown": metrics.get("max_drawdown"),
            "total_return": metrics.get("total_return"),
            "annual_return": metrics.get("annual_return"),
            "n_periods": metrics.get("n_periods"),
            "nav_curve": nav_curve_pts,
            "missing": False,
        })
    return out


def _to_uuid(s: str):
    """容错：把字符串转 uuid.UUID。"""
    import uuid as _uuid
    try:
        return _uuid.UUID(str(s))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"非法 UUID：{s!r}") from exc


# ── helpers ─────────────────────────────────────────────
def _coerce_date(d: Any) -> date:
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return date.fromisoformat(d)
    raise TypeError(f"无法转 date：{d!r}")


def _row_to_dict(r: FactBacktestRun) -> dict[str, Any]:
    return {
        "run_id": str(r.id),
        "job_id": str(r.job_id) if r.job_id else None,
        "business_date": r.business_date.isoformat() if r.business_date else "",
        "status": r.status,
        "config_hash": r.config_hash,
        "config_json": dict(r.config_json or {}),
        "result_json": dict(r.result_json or {}) if r.result_json else None,
        "error_message": r.error_message,
        "cost_total_pct": float(r.cost_total_pct) if r.cost_total_pct is not None else None,
        "benchmark_alpha": float(r.benchmark_alpha) if r.benchmark_alpha is not None else None,
        "benchmark_beta": float(r.benchmark_beta) if r.benchmark_beta is not None else None,
        "attribution_json": dict(r.attribution_json or {}) if r.attribution_json else None,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }
