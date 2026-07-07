"""services/tenant — 多租户管理（P5.3）。

设计：
- tenant_id 是 String(64)，nullable（NULL = 共享空间 / 默认租户）
- 默认租户常量：`DEFAULT_TENANT = "default"`
- 用法：
    - 写数据时显式带 tenant_id（来自请求头 X-Tenant-Id 或调用方传参）
    - 查询时按 tenant_id 过滤（共享维度不强制）
- 服务方法：
    - `current_tenant_id()`         解析当前请求 tenant（无 → default）
    - `list_tenants_with_usage()`   全量 tenant + 数据量
    - `get_tenant_usage(tenant_id)` 单租户详细

DB 查询走 AsyncSession 聚合，本期不强制依赖 DB 也能跑（DB 失败 → 降级返默认）。
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TENANT = "default"


def current_tenant_id(explicit: str | None = None) -> str:
    """解析当前请求的 tenant_id。

    优先级：显式参数 > 环境变量 ASHARE_TENANT > "default"
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("ASHARE_TENANT", "").strip()
    if env:
        return env
    return DEFAULT_TENANT


# ── DB 聚合（async）──────────────────────────


async def _aggregate_tenant_counts() -> dict[str, dict[str, int]]:
    """聚合各表 tenant_id 行数。失败返空 dict（调用方降级）。"""
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

    tables = {
        "fact_market_bar": FactMarketBar,
        "fact_capital_flow": FactCapitalFlow,
        "fact_event": FactEvent,
        "fact_event_raw": FactEventRaw,
        "fact_rule_hit": FactRuleHit,
        "fact_risk_alert": FactRiskAlert,
        "fact_strategy_output": FactStrategyOutput,
        "fact_decision_trace": FactDecisionTrace,
        "fact_backtest_run": FactBacktestRun,
    }
    out: dict[str, dict[str, int]] = {}
    try:
        async with AsyncSessionLocal() as session:
            for tbl_name, model in tables.items():
                rows = await session.execute(
                    select(model.tenant_id, func.count())
                    .group_by(model.tenant_id),
                )
                per_tenant: dict[str, int] = {}
                for tid, cnt in rows.all():
                    key = tid if tid is not None else "(null)"
                    per_tenant[key] = cnt
                out[tbl_name] = per_tenant
    except Exception as exc:
        logger.warning("聚合 tenant 行数失败：%s", exc)
        return {}
    return out


async def list_tenants_with_usage() -> list[dict[str, Any]]:
    """列出所有 tenant + 数据量。"""
    counts = await _aggregate_tenant_counts()
    if not counts:
        return [{"tenant_id": DEFAULT_TENANT, "tables": {}, "total_rows": 0}]
    # 收集所有 tenant_id（包含 null）
    tenant_ids: set[str] = set()
    for per in counts.values():
        tenant_ids.update(per.keys())
    out: list[dict[str, Any]] = []
    for tid in sorted(tenant_ids):
        per_table = {tbl: counts.get(tbl, {}).get(tid, 0) for tbl in counts}
        total = sum(per_table.values())
        out.append({
            "tenant_id": tid,
            "tables": per_table,
            "total_rows": total,
        })
    return out


async def get_tenant_usage(tenant_id: str) -> dict[str, Any] | None:
    """单租户详细使用量。"""
    counts = await _aggregate_tenant_counts()
    if not counts:
        return {"tenant_id": tenant_id, "tables": {}, "total_rows": 0}
    per_table = {tbl: counts.get(tbl, {}).get(tenant_id, 0) for tbl in counts}
    total = sum(per_table.values())
    return {"tenant_id": tenant_id, "tables": per_table, "total_rows": total}
