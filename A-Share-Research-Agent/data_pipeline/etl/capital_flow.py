"""CapitalFlowSyncJob — fact_capital_flow 同步。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import EntityType, FlowType, JobType, WindowType
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.etl._common import load_sector_security_mapping, load_security_rows
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.normalizers import CapitalFlowNormalizer
from data_pipeline.ops_audit import OpsAuditService
from models.facts import FactCapitalFlow

logger = logging.getLogger(__name__)


class CapitalFlowSyncJob:
    """资金流同步：市场整体资金 / 北向 / 个股资金 / 板块资金（按个股聚合）。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()

    async def run_close_flows(
        self,
        session: AsyncSession,
        business_date: date,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        idempotency_key = f"collector:fact_capital_flow:{business_date.isoformat()}"
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "capital_flow_close_sync", "symbols": symbols or []},
        )

        try:
            flow_frames: list[pd.DataFrame] = []

            market_flow_df = await self.client.get_market_fund_flow()
            normalized_market = CapitalFlowNormalizer.market_main_force(
                market_flow_df,
                business_date=business_date,
                source="akshare.stock_market_fund_flow",
                version=settings.DEFAULT_FEATURE_VERSION,
            )
            if not normalized_market.empty:
                flow_frames.append(normalized_market)

            northbound_df = await self.client.get_northbound_fund_flow_summary()
            normalized_northbound = CapitalFlowNormalizer.northbound_summary(
                northbound_df,
                business_date=business_date,
                source="akshare.stock_hsgt_fund_flow_summary_em",
                version=settings.DEFAULT_FEATURE_VERSION,
            )
            if not normalized_northbound.empty:
                flow_frames.append(normalized_northbound)

            failures: list[dict[str, str]] = []
            security_rows = await load_security_rows(session, symbols=symbols) if symbols else []
            for symbol, security_id, exchange in security_rows:
                try:
                    individual_df = await self.client.get_individual_fund_flow(
                        symbol=symbol,
                        market=exchange,
                    )
                    normalized_individual = CapitalFlowNormalizer.individual_main_force(
                        individual_df,
                        security_id=security_id,
                        business_date=business_date,
                        source="akshare.stock_individual_fund_flow",
                        version=settings.DEFAULT_FEATURE_VERSION,
                    )
                    if not normalized_individual.empty:
                        flow_frames.append(normalized_individual)
                except Exception as exc:
                    logger.warning("同步个股资金流失败, symbol=%s, error=%s", symbol, exc)
                    failures.append({"symbol": symbol, "error": str(exc)})

            sector_flow_df = await self._aggregate_sector_flows(session, business_date)
            if not sector_flow_df.empty:
                flow_frames.append(sector_flow_df)

            merged_df = pd.concat(flow_frames, ignore_index=True) if flow_frames else pd.DataFrame()
            if not merged_df.empty:
                await upsert_dataframe(
                    session=session,
                    df=merged_df,
                    model=FactCapitalFlow,
                    index_elements=[
                        "entity_type",
                        "entity_id",
                        "business_date",
                        "window_type",
                        "flow_type",
                        "version",
                    ],
                )

            await OpsAuditService.finish_job_success(
                session,
                job_id,
                trace_payload={
                    "synced_count": len(merged_df),
                    "failure_count": len(failures),
                    "failures": failures[:20],
                },
            )
            return {
                "synced_count": len(merged_df),
                "failure_count": len(failures),
                "failures": failures,
            }
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise

    @staticmethod
    async def _aggregate_sector_flows(
        session: AsyncSession,
        business_date: date,
    ) -> pd.DataFrame:
        """从已落库的事实表，按 security→sector 映射聚合板块级资金流。"""
        mapping_rows = await load_sector_security_mapping(session, business_date)
        if not mapping_rows:
            return pd.DataFrame()

        mapping_df = pd.DataFrame(mapping_rows, columns=["security_id", "sector_id"])
        flow_query = (
            select(
                FactCapitalFlow.entity_id,
                FactCapitalFlow.net_inflow,
                FactCapitalFlow.gross_inflow,
                FactCapitalFlow.gross_outflow,
            )
            .where(FactCapitalFlow.business_date == business_date)
            .where(FactCapitalFlow.entity_type == EntityType.SECURITY.value)
            .where(FactCapitalFlow.flow_type == FlowType.INDIVIDUAL_MAIN_FORCE.value)
        )
        flow_rows = (await session.execute(flow_query)).all()
        if not flow_rows:
            return pd.DataFrame()

        individual_flow_df = pd.DataFrame(
            flow_rows,
            columns=["security_id", "net_inflow", "gross_inflow", "gross_outflow"],
        )
        merged_df = mapping_df.merge(individual_flow_df, on="security_id", how="inner")
        if merged_df.empty:
            return pd.DataFrame()

        grouped = (
            merged_df.groupby("sector_id", as_index=False)[["net_inflow", "gross_inflow", "gross_outflow"]]
            .sum(numeric_only=False)
        )

        sector_frames = [
            CapitalFlowNormalizer.sector_main_force(
                sector_id=row["sector_id"],
                business_date=business_date,
                net_inflow=row["net_inflow"],
                gross_inflow=row["gross_inflow"],
                gross_outflow=row["gross_outflow"],
                source="aggregated.security_individual_flow",
                version=settings.DEFAULT_FEATURE_VERSION,
            )
            for _, row in grouped.iterrows()
        ]
        return pd.concat(sector_frames, ignore_index=True) if sector_frames else pd.DataFrame()
