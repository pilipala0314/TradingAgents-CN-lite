"""SecurityMasterSyncJob — dim_security 主数据同步。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import JobType, WindowType
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.normalizers import SecurityNormalizer
from data_pipeline.ops_audit import OpsAuditService
from models.dimensions import DimSecurity

logger = logging.getLogger(__name__)


class SecurityMasterSyncJob:
    """从 AkShare 拉取全 A 股证券列表，标准化入库 dim_security。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()

    async def run(self, session: AsyncSession, business_date: date) -> dict[str, Any]:
        idempotency_key = f"collector:dim_security:{business_date.isoformat()}"
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "security_master_sync"},
        )

        try:
            stock_df = await self.client.get_stock_list()
            normalized_df = SecurityNormalizer.from_spot_list(stock_df)
            await upsert_dataframe(
                session=session,
                df=normalized_df,
                model=DimSecurity,
                index_elements=["symbol"],
            )
            await OpsAuditService.finish_job_success(
                session,
                job_id,
                trace_payload={"synced_count": len(normalized_df)},
            )
            return {"synced_count": len(normalized_df)}
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise
