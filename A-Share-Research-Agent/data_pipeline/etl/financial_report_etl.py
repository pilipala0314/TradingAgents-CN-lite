"""FinancialReportETL — 财务报表 ETL（P2-T3）。

设计：
- provider 抽象：注入 FinancialReportProvider（默认），不直接调 akshare。
- 单只股票 ETL：拉长表 → 补 security_id → upsert fact_financial_report。
- 失败语义：
  - provider 抛错 → ETL 透传。
  - 数据为空（个股无财报）→ 返回 0/0 不抛错。
- 唯一键 (security_id, report_period, indicator_code, version)，可幂等重跑。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import JobType, WindowType
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.ops_audit import OpsAuditService
from data_pipeline.providers.financial_report_provider import FinancialReportProvider
from models.facts import FactFinancialReport

logger = logging.getLogger(__name__)


FINANCIAL_SOURCE_TAG = "akshare.stock_financial_analysis_indicator_em"


class FinancialReportETL:
    """财报 ETL 编排器。"""

    def __init__(self, provider: FinancialReportProvider | None = None) -> None:
        self.provider = provider or FinancialReportProvider()

    async def run_indicators(
        self,
        session: AsyncSession,
        *,
        business_date: date,
        security_id: Any,  # UUID
        symbol: str,
        start_year: str | None = None,
        end_year: str | None = None,
    ) -> dict[str, Any]:
        """单只股票 × 当日 → 拉长表 → upsert。

        Returns:
            {"synced_count": int, "failure_count": 0, "failures": []}
        """
        idempotency_key = (
            f"collector:fact_financial_report:{symbol}:{business_date.isoformat()}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "financial_report_sync", "symbol": symbol},
        )

        try:
            long_df = await self.provider.get_indicators(
                symbol=symbol, start_year=start_year, end_year=end_year,
            )
            if long_df.empty:
                logger.warning(
                    "FinancialReportETL.run_indicators: %s 无财报数据", symbol,
                )
                await OpsAuditService.finish_job_success(
                    session, job_id,
                    trace_payload={"synced_count": 0, "failure_count": 0, "note": "no_financial_data"},
                )
                return {"synced_count": 0, "failure_count": 0, "failures": []}

            # 补 security_id + version
            long_df = long_df.copy()
            long_df["security_id"] = security_id
            long_df["version"] = settings.DEFAULT_FEATURE_VERSION

            synced_count = await upsert_dataframe(
                session=session,
                df=long_df,
                model=FactFinancialReport,
                index_elements=["security_id", "report_period", "indicator_code", "version"],
            )

            await OpsAuditService.finish_job_success(
                session, job_id,
                trace_payload={
                    "synced_count": synced_count,
                    "symbol": symbol,
                    "source": FINANCIAL_SOURCE_TAG,
                },
            )
            logger.info(
                "FinancialReportETL.run_indicators synced=%d symbol=%s",
                synced_count, symbol,
            )
            return {"synced_count": synced_count, "failure_count": 0, "failures": []}
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise
