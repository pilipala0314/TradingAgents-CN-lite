"""services/retention — 数据保留策略（P5.3）。

设计：
- 每张表配置"最大保留天数" + "保留最少行数"（防止误删光）
- 周期 / 手动触发清理超龄行
- dry_run 默认 True（生产先 dry_run 看影响）

策略表（默认）：
- fact_market_bar         730d
- fact_capital_flow       365d
- fact_event_raw          90d   (raw 留 3 个月够审计)
- fact_event              730d
- fact_rule_hit           365d
- fact_risk_alert         365d
- fact_strategy_output    365d
- fact_decision_trace     365d
- fact_backtest_run       180d
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── 默认策略 ──────────────────────────────────

DEFAULT_POLICY: dict[str, dict[str, int]] = {
    "fact_market_bar":      {"retain_days": 730, "min_rows": 1_000_000},
    "fact_capital_flow":    {"retain_days": 365, "min_rows": 100_000},
    "fact_event_raw":       {"retain_days": 90,  "min_rows": 0},
    "fact_event":           {"retain_days": 730, "min_rows": 10_000},
    "fact_rule_hit":        {"retain_days": 365, "min_rows": 10_000},
    "fact_risk_alert":      {"retain_days": 365, "min_rows": 1_000},
    "fact_strategy_output": {"retain_days": 365, "min_rows": 1_000},
    "fact_decision_trace":  {"retain_days": 365, "min_rows": 1_000},
    "fact_backtest_run":    {"retain_days": 180, "min_rows": 100},
}


def get_policy() -> dict[str, dict[str, int]]:
    """当前策略（未来支持从 settings / DB 读；本期返默认 + env 覆盖）。"""
    pol = dict(DEFAULT_POLICY)
    # env 覆盖：RETENTION_<TABLE>_DAYS=...
    for tbl in pol:
        env_key = f"RETENTION_{tbl.upper()}_DAYS"
        v = os.environ.get(env_key, "").strip()
        if v.isdigit():
            pol[tbl]["retain_days"] = int(v)
    return pol


# ── 字段映射（表 → 时间列）─────────────

_TABLE_DATE_COL: dict[str, str] = {
    "fact_market_bar":      "bar_date",
    "fact_capital_flow":    "business_date",
    "fact_event_raw":       "business_date",
    "fact_event":           "published_at",
    "fact_rule_hit":        "business_date",
    "fact_risk_alert":      "business_date",
    "fact_strategy_output": "business_date",
    "fact_decision_trace":  "business_date",
    "fact_backtest_run":    "business_date",
}


# ── 状态查询 ──────────────────────────────


async def _count_table(table_name: str) -> int:
    from sqlalchemy import func, select

    from core.database import AsyncSessionLocal
    from models.facts import (
        FactCapitalFlow,
        FactEvent,
        FactEventRaw,
        FactMarketBar,
        FactRuleHit,
    )
    from models.outputs import (
        FactBacktestRun,
        FactDecisionTrace,
        FactRiskAlert,
        FactStrategyOutput,
    )
    model = {
        "fact_market_bar": FactMarketBar,
        "fact_capital_flow": FactCapitalFlow,
        "fact_event": FactEvent,
        "fact_event_raw": FactEventRaw,
        "fact_rule_hit": FactRuleHit,
        "fact_risk_alert": FactRiskAlert,
        "fact_strategy_output": FactStrategyOutput,
        "fact_decision_trace": FactDecisionTrace,
        "fact_backtest_run": FactBacktestRun,
    }.get(table_name)
    if model is None:
        return -1
    try:
        async with AsyncSessionLocal() as session:
            r = await session.execute(select(func.count()).select_from(model))
            return int(r.scalar() or 0)
    except Exception as exc:
        logger.warning("count %s 失败：%s", table_name, exc)
        return -1


async def get_retention_status() -> dict[str, Any]:
    """当前策略 + 每张表行数。"""
    pol = get_policy()
    table_status: dict[str, dict[str, Any]] = {}
    for tbl, cfg in pol.items():
        rows = await _count_table(tbl)
        table_status[tbl] = {
            "retain_days": cfg["retain_days"],
            "min_rows": cfg["min_rows"],
            "current_rows": rows,
        }
    return {"policy": pol, "tables": table_status, "checked_at": datetime.now(timezone.utc).isoformat()}


# ── 清理执行 ────────────────────────────


async def _delete_older_than(table_name: str, days: int, min_rows: int, dry_run: bool) -> int:
    from sqlalchemy import delete, func, select

    from core.database import AsyncSessionLocal
    from models.facts import (
        FactCapitalFlow,
        FactEvent,
        FactEventRaw,
        FactMarketBar,
        FactRuleHit,
    )
    from models.outputs import (
        FactBacktestRun,
        FactDecisionTrace,
        FactRiskAlert,
        FactStrategyOutput,
    )
    model = {
        "fact_market_bar": FactMarketBar,
        "fact_capital_flow": FactCapitalFlow,
        "fact_event": FactEvent,
        "fact_event_raw": FactEventRaw,
        "fact_rule_hit": FactRuleHit,
        "fact_risk_alert": FactRiskAlert,
        "fact_strategy_output": FactStrategyOutput,
        "fact_decision_trace": FactDecisionTrace,
        "fact_backtest_run": FactBacktestRun,
    }.get(table_name)
    if model is None:
        return 0
    col_name = _TABLE_DATE_COL.get(table_name, "business_date")
    col = getattr(model, col_name, None)
    if col is None:
        return 0
    cutoff_date = date.today() - timedelta(days=days)
    try:
        async with AsyncSessionLocal() as session:
            # 当前总行数
            total = (await session.execute(select(func.count()).select_from(model))).scalar() or 0
            if total <= min_rows:
                return 0
            # 待删行数
            count_stmt = select(func.count()).select_from(model).where(col < cutoff_date)
            to_delete = (await session.execute(count_stmt)).scalar() or 0
            if to_delete == 0:
                return 0
            if dry_run:
                return int(to_delete)
            # 真删
            stmt = delete(model).where(col < cutoff_date)
            result = await session.execute(stmt)
            await session.commit()
            return int(result.rowcount or to_delete)
    except Exception as exc:
        logger.warning("清理 %s 失败：%s", table_name, exc)
        return -1


async def run_retention(dry_run: bool = True) -> dict[str, Any]:
    """跑一轮清理。返回 {table: deleted_count}。"""
    pol = get_policy()
    result: dict[str, Any] = {"dry_run": dry_run, "deleted": {}, "errors": [], "ran_at": datetime.now(timezone.utc).isoformat()}
    for tbl, cfg in pol.items():
        n = await _delete_older_than(
            tbl,
            days=cfg["retain_days"],
            min_rows=cfg["min_rows"],
            dry_run=dry_run,
        )
        if n < 0:
            result["errors"].append(tbl)
        else:
            result["deleted"][tbl] = n
    return result
