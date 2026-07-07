"""MarketBarSyncJob — fact_market_bar 日 K 同步。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import JobType, WindowType
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.etl._common import load_security_rows
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.normalizers import MarketBarNormalizer
from data_pipeline.ops_audit import OpsAuditService
from models.facts import FactMarketBar

logger = logging.getLogger(__name__)


class MarketBarSyncJob:
    """从 AkShare 拉取个股日 K 行情，写入 fact_market_bar。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()

    async def run_daily_bars(
        self,
        session: AsyncSession,
        business_date: date,
        symbols: list[str] | None = None,
        adjust: str = "",
    ) -> dict[str, Any]:
        date_str = business_date.strftime("%Y%m%d")
        idempotency_key = f"collector:fact_market_bar:daily:{date_str}:{adjust or 'none'}"
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "market_bar_daily_sync", "symbols": symbols or "all"},
        )

        try:
            security_rows = await load_security_rows(session, symbols=symbols)
            if not security_rows:
                raise ValueError("未找到可同步的证券主数据，请先执行 SecurityMasterSyncJob。")

            bar_frames: list[pd.DataFrame] = []
            failures: list[dict[str, str]] = []
            for symbol, security_id, _ in security_rows:
                try:
                    raw_df = await self.client.get_stock_daily_hist(
                        symbol=symbol,
                        start_date=date_str,
                        end_date=date_str,
                        adjust=adjust,
                    )
                    normalized_df = MarketBarNormalizer.from_history(
                        raw_df=raw_df,
                        security_id=security_id,
                        source="akshare.stock_zh_a_hist",
                        bar_timeframe="1d",
                        adj_type=adjust or "none",
                        version=settings.DEFAULT_FEATURE_VERSION,
                    )
                    if not normalized_df.empty:
                        bar_frames.append(normalized_df)
                except Exception as exc:
                    logger.warning("同步个股日线失败, symbol=%s, error=%s", symbol, exc)
                    failures.append({"symbol": symbol, "error": str(exc)})

            merged_df = pd.concat(bar_frames, ignore_index=True) if bar_frames else pd.DataFrame()
            if not merged_df.empty:
                await upsert_dataframe(
                    session=session,
                    df=merged_df,
                    model=FactMarketBar,
                    index_elements=[
                        "security_id",
                        "bar_date",
                        "bar_timeframe",
                        "adj_type",
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
