"""MarketDataSyncJob — feature_market_snapshot 同步。"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import EntityType, WindowType
from core.identifiers import MARKET_ENTITY_ID
from data_pipeline.akshare_client import AkShareClient
from data_pipeline.loaders.db_sync import upsert_dataframe
from features_engine.snapshot_mgr import SnapshotManager
from models.facts import FactRuleHit
from models.features import FeatureMarketSnapshot
from rule_engine.evaluator import RuleEngine

logger = logging.getLogger(__name__)


class MarketDataSyncJob:
    """盘后市场数据采集 + 市场快照生成 + 市场层规则命中落库。"""

    def __init__(self, client: AkShareClient | None = None) -> None:
        self.client = client or AkShareClient()

    async def run_close_snapshot(
        self,
        session: AsyncSession,
        business_date: date,
        market_overview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        logger.info("开始执行盘后市场快照任务，business_date=%s", business_date)
        trading_date = business_date.strftime("%Y%m%d")
        market_overview = market_overview or {}

        limit_up_df = await self.client.get_limit_up_pool(trading_date)
        limit_down_df = await self.client.get_limit_down_pool(trading_date)
        bomb_board_df = await self.client.get_bomb_board_pool(trading_date)

        snapshot = SnapshotManager.build_market_snapshot(
            business_date=business_date,
            window_type=WindowType.CLOSE.value,
            market_overview=market_overview,
            limit_up_df=limit_up_df,
            limit_down_df=limit_down_df,
            bomb_board_df=bomb_board_df,
        )
        rule_hits = RuleEngine.evaluate_market(snapshot)

        await upsert_dataframe(
            session=session,
            df=self._snapshot_to_dataframe(snapshot),
            model=FeatureMarketSnapshot,
            index_elements=["business_date", "window_type", "feature_version"],
        )

        market_rule_hits: list[dict[str, Any]] = []
        for hit in rule_hits:
            hit.update(
                {
                    "entity_type": EntityType.MARKET.value,
                    "entity_id": MARKET_ENTITY_ID,
                    "business_date": business_date,
                    "window_type": WindowType.CLOSE.value,
                    "rule_version": settings.DEFAULT_FEATURE_VERSION,
                }
            )
            market_rule_hits.append(hit)

        if market_rule_hits:
            rule_df = pd.DataFrame(market_rule_hits)
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

        logger.info(
            "盘后市场快照任务完成，market_regime=%s，rule_hit_count=%s",
            snapshot["market_regime"],
            len(rule_hits),
        )
        return {"market_snapshot": snapshot, "rule_hits": rule_hits}

    @staticmethod
    def _snapshot_to_dataframe(snapshot: dict[str, Any]) -> pd.DataFrame:
        return pd.DataFrame([snapshot])
