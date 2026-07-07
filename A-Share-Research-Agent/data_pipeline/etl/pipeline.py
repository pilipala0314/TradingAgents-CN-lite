"""FactETLPipeline — 收盘 ETL 主链编排器。

阶段（按依赖顺序）：
1. SecurityMasterSyncJob   — dim_security
2. SectorMasterSyncJob     — dim_sector + map_security_sector
3. MarketBarSyncJob        — fact_market_bar
4. CapitalFlowSyncJob      — fact_capital_flow
5. EventIngestJob          — fact_event_raw + fact_event
6. MarketDataSyncJob       — feature_market_snapshot + fact_rule_hit (market)
7. SectorFeatureSyncJob    — feature_sector_snapshot + fact_rule_hit (sector)
8. StockFeatureSyncJob     — feature_stock_snapshot + fact_rule_hit (stock)
9. BSEKlineETL (P1-T11)    — 北交所 K 线
10. GEMKlineETL (P1-T12)   — 创业板 K 线
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from core.database import AsyncSessionLocal
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.etl.bse_kline_etl import BSEKlineETL
from data_pipeline.etl.capital_flow import CapitalFlowSyncJob
from data_pipeline.etl.event_ingest import EventIngestJob
from data_pipeline.etl.gem_kline_etl import GEMKlineETL
from data_pipeline.etl.market_bar import MarketBarSyncJob
from data_pipeline.etl.market_feature import MarketDataSyncJob
from data_pipeline.etl.sector_feature import SectorFeatureSyncJob
from data_pipeline.etl.sector_master import SectorMasterSyncJob
from data_pipeline.etl.security_master import SecurityMasterSyncJob
from data_pipeline.etl.stock_feature import StockFeatureSyncJob

logger = logging.getLogger(__name__)


class FactETLPipeline:
    """收盘 ETL 编排器。提供 `run_close_pipeline` 顶层入口。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()
        self.security_master = SecurityMasterSyncJob(self.client)
        self.sector_master = SectorMasterSyncJob(self.client)
        self.market_bar = MarketBarSyncJob(self.client)
        self.capital_flow = CapitalFlowSyncJob(self.client)
        self.event_ingest = EventIngestJob(self.client)
        self.market_feature = MarketDataSyncJob(self.client)
        self.sector_feature = SectorFeatureSyncJob(self.client)
        self.stock_feature = StockFeatureSyncJob(self.client)

    async def run_close_pipeline(
        self,
        business_date: date,
        *,
        symbols: list[str] | None = None,
        event_file_path: str | None = None,
    ) -> dict[str, Any]:
        logger.info(
            "=== FactETLPipeline.start business_date=%s symbols=%s ===",
            business_date,
            symbols or "ALL",
        )

        results: dict[str, Any] = {}

        async with AsyncSessionLocal() as session:
            results["security_master"] = await self.security_master.run(session, business_date)
            results["sector_master"] = await self.sector_master.run(session, business_date)
            await session.commit()

        async with AsyncSessionLocal() as session:
            results["market_bar"] = await self.market_bar.run_daily_bars(
                session, business_date, symbols=symbols
            )
            results["capital_flow"] = await self.capital_flow.run_close_flows(
                session, business_date, symbols=symbols
            )
            results["event_ingest"] = await self.event_ingest.run(
                session,
                business_date,
                symbols=symbols,
                event_file_path=event_file_path,
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            market_snapshot_payload = await self.market_feature.run_close_snapshot(
                session, business_date
            )
            results["market_feature"] = {
                "rule_hits": market_snapshot_payload["rule_hits"],
                "market_regime": market_snapshot_payload["market_snapshot"].get("market_regime"),
            }
            await session.commit()

        async with AsyncSessionLocal() as session:
            results["sector_feature"] = await self.sector_feature.run(
                session,
                business_date,
                market_snapshot=market_snapshot_payload["market_snapshot"],
            )
            results["stock_feature"] = await self.stock_feature.run(
                session, business_date, symbols=symbols
            )
            await session.commit()

        logger.info("=== FactETLPipeline.end business_date=%s ===", business_date)
        return results
