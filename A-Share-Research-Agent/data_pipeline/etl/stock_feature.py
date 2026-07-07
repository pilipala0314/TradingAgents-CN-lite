"""StockFeatureSyncJob — feature_stock_snapshot 同步。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import JobType, WindowType
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.etl._common import load_security_rows
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.ops_audit import OpsAuditService
from features_engine.calculators.stock_tactical import (
    calculate_stock_tactical_features,
)
from models.facts import FactRuleHit
from models.features import FeatureStockSnapshot

logger = logging.getLogger(__name__)


class StockFeatureSyncJob:
    """同步个股特征快照：技术 + 资金 + 事件 + 战术标签。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()

    async def run(
        self,
        session: AsyncSession,
        business_date: date,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        idempotency_key = (
            f"collector:feature_stock_snapshot:{business_date.isoformat()}"
        )
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "stock_feature_sync", "symbols": symbols or "all"},
        )

        try:
            security_rows = await load_security_rows(session, symbols=symbols)
            if not security_rows:
                raise ValueError("未找到证券主数据，请先执行 SecurityMasterSyncJob。")

            snapshot_rows: list[dict[str, Any]] = []
            rule_rows: list[dict[str, Any]] = []
            failures: list[dict[str, str]] = []

            for symbol, security_id, exchange in security_rows:
                try:
                    bar_df = await self.client.get_stock_daily_hist(
                        symbol=symbol,
                        start_date=(business_date.replace(year=business_date.year - 1)).strftime("%Y%m%d"),
                        end_date=business_date.strftime("%Y%m%d"),
                        adjust="qfq",
                    )
                    lhb_df = await self.client.get_stock_lhb(symbol=symbol)
                    flow_df = await self.client.get_individual_fund_flow(
                        symbol=symbol,
                        market=exchange,
                    )
                    news_df = await self.client.get_stock_news(symbol=symbol)
                except Exception as exc:
                    logger.warning("拉取个股事实失败, symbol=%s, error=%s", symbol, exc)
                    failures.append({"symbol": symbol, "error": str(exc)})
                    continue

                features = calculate_stock_tactical_features(
                    security_id=security_id,
                    business_date=business_date,
                    bar_df=bar_df,
                    lhb_df=lhb_df,
                    flow_df=flow_df,
                    news_df=news_df,
                )
                snapshot_rows.append(features)

            if snapshot_rows:
                snapshot_df = pd.DataFrame(snapshot_rows)
                await upsert_dataframe(
                    session=session,
                    df=snapshot_df,
                    model=FeatureStockSnapshot,
                    index_elements=["security_id", "business_date", "feature_version"],
                )

            if rule_rows:
                rule_df = pd.DataFrame(rule_rows)
                await upsert_dataframe(
                    session=session,
                    df=rule_df,
                    model=FactRuleHit,
                    index_elements=[
                        "entity_type",
                        "entity_id",
                        "business_date",
                        "window_type",
                        "rule_code",
                        "rule_version",
                    ],
                )

            await OpsAuditService.finish_job_success(
                session,
                job_id,
                trace_payload={
                    "snapshot_count": len(snapshot_rows),
                    "failure_count": len(failures),
                    "failures": failures[:20],
                },
            )
            return {
                "snapshot_count": len(snapshot_rows),
                "failure_count": len(failures),
                "failures": failures,
            }
        except Exception as exc:
            await OpsAuditService.finish_job_failed(session, job_id, str(exc))
            raise
