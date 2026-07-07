"""SectorFeatureSyncJob — feature_sector_snapshot 同步。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import JobType, WindowType
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.etl._common import load_sector_rows
from data_pipeline.loaders.db_sync import upsert_dataframe
from data_pipeline.ops_audit import OpsAuditService
from features_engine.calculators.sector_lifecycle import (
    calculate_sector_lifecycle_features,
)
from models.facts import FactRuleHit
from models.features import FeatureSectorSnapshot

logger = logging.getLogger(__name__)


class SectorFeatureSyncJob:
    """同步板块特征快照：基于行情/资金/事件事实算 sector_money_flow / event / state / mainline_strength / breadth / leader。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()

    async def run(
        self,
        session: AsyncSession,
        business_date: date,
        market_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        idempotency_key = f"collector:feature_sector_snapshot:{business_date.isoformat()}"
        job_id = await OpsAuditService.start_job(
            session,
            job_type=JobType.COLLECTOR.value,
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            idempotency_key=idempotency_key,
            trace_payload={"job": "sector_feature_sync"},
        )

        try:
            sector_rows = await load_sector_rows(
                session, sector_types=["industry", "concept"]
            )
            if not sector_rows:
                raise ValueError("未找到板块主数据，请先执行 SectorMasterSyncJob。")

            market_snapshot = market_snapshot or {"market_regime": "neutral_mixed"}
            business_date.strftime("%Y%m%d")

            snapshot_rows: list[dict[str, Any]] = []
            rule_rows: list[dict[str, Any]] = []
            failures: list[dict[str, str]] = []

            for _sector_code, sector_name, sector_id, sector_type in sector_rows:
                try:
                    bar_df = await self.client.get_sector_recent_bars(sector_name)
                    flow_df = await self.client.get_sector_fund_flow(sector_name)
                    leader_df = await self.client.get_sector_leaders(sector_name)
                    event_payload = await self.client.get_sector_event_signals(sector_name)
                except Exception as exc:
                    logger.warning("拉取板块事实失败, sector=%s, error=%s", sector_name, exc)
                    failures.append({"sector_name": sector_name, "error": str(exc)})
                    continue

                features = calculate_sector_lifecycle_features(
                    sector_id=sector_id,
                    sector_name=sector_name,
                    sector_type=sector_type,
                    business_date=business_date,
                    market_snapshot=market_snapshot,
                    bar_df=bar_df,
                    flow_df=flow_df,
                    leader_df=leader_df,
                    event_payload=event_payload,
                )
                snapshot_rows.append(features)

            if snapshot_rows:
                snapshot_df = pd.DataFrame(snapshot_rows)
                await upsert_dataframe(
                    session=session,
                    df=snapshot_df,
                    model=FeatureSectorSnapshot,
                    index_elements=["sector_id", "business_date", "feature_version"],
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
