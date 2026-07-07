"""MacroETL — 宏观数据 ETL（P0-T5）。

复用 `services.macro_service.get_macro_snapshot()` 抓 4 个宏观端点（PMI / CPI / M2 / 国债利差），
按 (indicator_code, period, version) 落库 `fact_macro_indicator`。

ETL 频率：建议每日 09:00 跑（PMI/CPI/M2 都是月度指标，新值通常月初公布）。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import JobType, WindowType
from data_pipeline.ops_audit import OpsAuditService
from models.macro import FactMacroIndicator, MacroIndicatorCode
from services.macro_service import MacroSnapshot, get_macro_snapshot

logger = logging.getLogger(__name__)


# 4 个宏观端点 → indicator_code 映射
_SNAPSHOT_TO_CODE: dict[str, str] = {
    "pmi": MacroIndicatorCode.PMI_MFG,
    "cpi": MacroIndicatorCode.CPI_YOY,
    "m2": MacroIndicatorCode.M2_YOY,
    "bond_yield": MacroIndicatorCode.CN_US_SPREAD,
}

# 每月初的 period 字符串（YYYY-MM）
def _period_from_asof(as_of: str | None) -> str:
    if as_of and len(as_of) >= 7:
        return as_of[:7]
    return datetime.utcnow().strftime("%Y-%m")


def _row_from_snapshot(
    snap: dict[str, Any], *, indicator_code: str, source: str, version: str,
) -> dict[str, Any] | None:
    """把 MacroSnapshot 的一段转成 FactMacroIndicator 入库 dict。"""
    if not snap or not snap.get("available"):
        return None
    value = snap.get("value")
    if value is None:
        return None
    as_of = snap.get("as_of")
    period = _period_from_asof(as_of)
    raw = snap.get("raw") or {}
    return {
        "indicator_code": indicator_code,
        "period": period,
        "value": float(value) if isinstance(value, (int, float)) else None,
        "as_of": as_of,
        "source": source,
        "version": version,
        "payload": json.loads(json.dumps(raw, default=str)),  # JSON-safe
        "tenant_id": None,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }


async def _upsert_indicator(
    session: AsyncSession, row: dict[str, Any],
) -> int:
    """upsert 一行；返回 1 = 新增/更新，0 = 跳过。"""
    stmt = pg_insert(FactMacroIndicator).values(**row)
    # on conflict：只更新 value/as_of/payload/updated_at
    stmt = stmt.on_conflict_do_update(
        constraint="uq_fact_macro_indicator_natural_key",
        set_={
            "value": stmt.excluded.value,
            "as_of": stmt.excluded.as_of,
            "payload": stmt.excluded.payload,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)
    return 1


class MacroETL:
    """宏观数据 ETL 编排器。

    用法：
    ```python
    etl = MacroETL()
    result = await etl.run_daily_ingest(session, business_date=date.today())
    ```
    """

    def __init__(self) -> None:
        self._version = settings.DEFAULT_FEATURE_VERSION

    async def run_daily_ingest(
        self, session: AsyncSession, *, business_date: date,
    ) -> dict[str, Any]:
        """每日 09:00 拉 4 端宏观数据 → upsert fact_macro_indicator。

        Returns:
            {"upserted_count": int, "per_indicator": {code: bool}}
        """
        idempotency_key = (
            f"collector:fact_macro_indicator:daily:{business_date.isoformat()}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.INTRADAY.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "macro_indicator_daily_ingest"},
        )
        try:
            snapshot: MacroSnapshot = await get_macro_snapshot()
            per_indicator: dict[str, bool] = {}
            upserted_count = 0
            for snap_key, code in _SNAPSHOT_TO_CODE.items():
                snap = getattr(snapshot, snap_key)
                source = f"akshare.macro_{snap_key}"
                row = _row_from_snapshot(
                    snap, indicator_code=code, source=source, version=self._version,
                )
                if row is None:
                    per_indicator[code] = False
                    continue
                await _upsert_indicator(session, row)
                per_indicator[code] = True
                upserted_count += 1

            await OpsAuditService.finish_job_success(
                session, job_id,
                trace_payload={
                    "upserted_count": upserted_count,
                    "per_indicator": per_indicator,
                },
            )
            logger.info(
                "MacroETL.run_daily_ingest upserted=%d business_date=%s",
                upserted_count, business_date,
            )
            return {
                "upserted_count": upserted_count,
                "per_indicator": per_indicator,
            }
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise

    async def fetch_latest_snapshot(
        self, session: AsyncSession, *, indicator_code: str, limit: int = 12,
    ) -> list[dict[str, Any]]:
        """从 DB 读某个 indicator 的最近 N 期（默认 12 月）。"""
        stmt = (
            select(FactMacroIndicator)
            .where(FactMacroIndicator.indicator_code == indicator_code)
            .order_by(FactMacroIndicator.period.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "indicator_code": r.indicator_code,
                "period": r.period,
                "value": r.value,
                "as_of": r.as_of,
                "source": r.source,
            }
            for r in rows
        ]
