"""EventIngestJob — 事件标准化 (fact_event_raw → fact_event)。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import JobType, WindowType
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.normalizers.event_normalizer import EventNormalizer
from data_pipeline.ops_audit import OpsAuditService
from models.dimensions import DimSecurity
from models.facts import FactEvent, FactEventRaw

logger = logging.getLogger(__name__)


class EventIngestJob:
    """事件层：拉取 AkShare 公告/快讯 → 标准化写入 fact_event_raw + fact_event。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()

    async def run(
        self,
        session: AsyncSession,
        business_date: date,
        symbols: list[str] | None = None,
        event_file_path: str | None = None,
    ) -> dict[str, Any]:
        idempotency_key = f"collector:fact_event:{business_date.isoformat()}"
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "event_ingest", "event_file": event_file_path},
        )

        try:
            raw_events: list[dict[str, Any]] = []
            if event_file_path:
                raw_events.extend(
                    EventNormalizer.load_external_event_file(event_file_path)
                )
            else:
                raw_events.extend(await self.client.get_global_news_aggregated(business_date))

            if not raw_events:
                await OpsAuditService.finish_job_success(
                    session, job_id, trace_payload={"raw_count": 0, "normalized_count": 0}
                )
                return {"raw_count": 0, "normalized_count": 0}

            raw_df = EventNormalizer.raw_to_dataframe(
                raw_events, business_date=business_date
            )
            await upsert_dataframe(
                session=session,
                df=raw_df,
                model=FactEventRaw,
                index_elements=["event_id"],
            )

            symbol_map = await self._load_symbol_map(session)
            normalized = EventNormalizer.normalize_events(
                raw_events, business_date=business_date, symbol_map=symbol_map
            )
            if normalized:
                import pandas as pd

                normalized_df = pd.DataFrame(normalized)
                await upsert_dataframe(
                    session=session,
                    df=normalized_df,
                    model=FactEvent,
                    index_elements=["event_id", "event_version"],
                )

            await OpsAuditService.finish_job_success(
                session,
                job_id,
                trace_payload={
                    "raw_count": len(raw_df),
                    "normalized_count": len(normalized),
                },
            )
            return {
                "raw_count": len(raw_df),
                "normalized_count": len(normalized),
            }
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise

    @staticmethod
    async def _load_symbol_map(session: AsyncSession) -> dict[str, Any]:
        result = await session.execute(
            select(DimSecurity.symbol, DimSecurity.security_id, DimSecurity.name)
        )
        mapping: dict[str, Any] = {}
        for symbol, security_id, name in result.all():
            mapping[symbol] = {"security_id": security_id, "name": name}
            if name:
                mapping[name] = {"security_id": security_id, "symbol": symbol}
        return mapping
