"""SectorMasterSyncJob — dim_sector + map_security_sector 同步。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import JobType, WindowType
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.etl._common import (
    load_sector_rows,
    load_security_map,
)
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.normalizers import SectorNormalizer
from data_pipeline.ops_audit import OpsAuditService
from models.dimensions import DimSector, MapSecuritySector

logger = logging.getLogger(__name__)


class SectorMasterSyncJob:
    """同步 dim_sector（行业+概念板块）与 map_security_sector（成分股映射）。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()

    async def run(self, session: AsyncSession, business_date: date) -> dict[str, Any]:
        idempotency_key = f"collector:dim_sector:{business_date.isoformat()}"
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "sector_master_sync"},
        )

        try:
            concept_df = await self.client.get_concept_board_list()
            industry_df = await self.client.get_industry_board_list()

            sector_frames = [
                SectorNormalizer.from_board_list(
                    concept_df,
                    sector_type="concept",
                    source="akshare.stock_board_concept_name_em",
                ),
                SectorNormalizer.from_board_list(
                    industry_df,
                    sector_type="industry",
                    source="akshare.stock_board_industry_name_em",
                ),
            ]
            sector_df = pd.concat(sector_frames, ignore_index=True)
            if not sector_df.empty:
                await upsert_dataframe(
                    session=session,
                    df=sector_df,
                    model=DimSector,
                    index_elements=["sector_code", "source"],
                )

            security_map = await load_security_map(session)
            sector_rows = await load_sector_rows(session, sector_types=["concept", "industry"])
            mapping_frames: list[pd.DataFrame] = []
            failures: list[dict[str, str]] = []

            for _sector_code, sector_name, sector_id, sector_type in sector_rows:
                try:
                    if sector_type == "industry":
                        component_df = await self.client.get_industry_board_components(sector_name)
                    else:
                        component_df = await self.client.get_concept_board_components(sector_name)
                    mapping_df = SectorNormalizer.build_mapping(
                        component_df,
                        security_map=security_map,
                        sector_id=sector_id,
                        business_date=business_date,
                    )
                    if not mapping_df.empty:
                        mapping_frames.append(mapping_df)
                except Exception as exc:
                    logger.warning("同步板块成分失败, sector=%s, error=%s", sector_name, exc)
                    failures.append({"sector_name": sector_name, "error": str(exc)})

            merged_mapping_df = (
                pd.concat(mapping_frames, ignore_index=True) if mapping_frames else pd.DataFrame()
            )
            if not merged_mapping_df.empty:
                await upsert_dataframe(
                    session=session,
                    df=merged_mapping_df,
                    model=MapSecuritySector,
                    index_elements=["security_id", "sector_id", "effective_from"],
                )

            await OpsAuditService.finish_job_success(
                session,
                job_id,
                trace_payload={
                    "sector_count": len(sector_df),
                    "mapping_count": len(merged_mapping_df),
                    "failure_count": len(failures),
                    "failures": failures[:20],
                },
            )
            return {
                "sector_count": len(sector_df),
                "mapping_count": len(merged_mapping_df),
                "failure_count": len(failures),
                "failures": failures,
            }
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise
