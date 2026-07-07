from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import EntityType
from core.identifiers import MARKET_ENTITY_ID
from models.facts import FactRuleHit
from models.features import FeatureMarketSnapshot, FeatureSectorSnapshot


@dataclass(slots=True)
class ResearchContext:
    market_snapshot: dict[str, Any]
    market_rule_hits: list[dict[str, Any]]
    sector_snapshots: list[dict[str, Any]]
    sector_rule_hits_map: dict[str, list[dict[str, Any]]]


class ResearchContextLoader:
    """Load the latest persisted research context for LangGraph execution."""

    MARKET_ENTITY_ID = MARKET_ENTITY_ID

    @classmethod
    async def load(
        cls,
        session: AsyncSession,
        *,
        business_date: date,
        window_type: str,
    ) -> ResearchContext:
        market_snapshot = await cls._load_market_snapshot(
            session=session,
            business_date=business_date,
            window_type=window_type,
        )
        if market_snapshot is None:
            raise LookupError(
                f"未找到 market_snapshot: business_date={business_date.isoformat()}, "
                f"window_type={window_type}"
            )

        sector_snapshots = await cls._load_sector_snapshots(
            session=session,
            business_date=business_date,
            window_type=window_type,
        )
        market_rule_hits = await cls._load_market_rule_hits(
            session=session,
            business_date=business_date,
            window_type=window_type,
        )
        sector_rule_hits_map = await cls._load_sector_rule_hits(
            session=session,
            business_date=business_date,
            window_type=window_type,
        )

        return ResearchContext(
            market_snapshot=market_snapshot,
            market_rule_hits=market_rule_hits,
            sector_snapshots=sector_snapshots,
            sector_rule_hits_map=sector_rule_hits_map,
        )

    @classmethod
    async def _load_market_snapshot(
        cls,
        session: AsyncSession,
        *,
        business_date: date,
        window_type: str,
    ) -> dict[str, Any] | None:
        stmt = (
            select(FeatureMarketSnapshot)
            .where(FeatureMarketSnapshot.business_date == business_date)
            .where(FeatureMarketSnapshot.window_type == window_type)
            .where(FeatureMarketSnapshot.feature_version == settings.DEFAULT_FEATURE_VERSION)
            .limit(1)
        )
        row = (await session.execute(stmt)).scalar_one_or_none()
        return cls._serialize_model(row) if row is not None else None

    @classmethod
    async def _load_sector_snapshots(
        cls,
        session: AsyncSession,
        *,
        business_date: date,
        window_type: str,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(FeatureSectorSnapshot)
            .where(FeatureSectorSnapshot.business_date == business_date)
            .where(FeatureSectorSnapshot.window_type == window_type)
            .where(FeatureSectorSnapshot.feature_version == settings.DEFAULT_FEATURE_VERSION)
            .order_by(FeatureSectorSnapshot.continuation_score.desc().nullslast())
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [cls._serialize_model(row) for row in rows]

    @classmethod
    async def _load_market_rule_hits(
        cls,
        session: AsyncSession,
        *,
        business_date: date,
        window_type: str,
    ) -> list[dict[str, Any]]:
        stmt = (
            select(FactRuleHit)
            .where(FactRuleHit.entity_type == EntityType.MARKET.value)
            .where(FactRuleHit.entity_id == cls.MARKET_ENTITY_ID)
            .where(FactRuleHit.business_date == business_date)
            .where(FactRuleHit.window_type == window_type)
            .where(FactRuleHit.rule_version == settings.DEFAULT_RULE_VERSION)
            .order_by(FactRuleHit.created_at.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [cls._serialize_model(row) for row in rows]

    @classmethod
    async def _load_sector_rule_hits(
        cls,
        session: AsyncSession,
        *,
        business_date: date,
        window_type: str,
    ) -> dict[str, list[dict[str, Any]]]:
        stmt = (
            select(FactRuleHit)
            .where(FactRuleHit.entity_type == EntityType.SECTOR.value)
            .where(FactRuleHit.business_date == business_date)
            .where(FactRuleHit.window_type == window_type)
            .where(FactRuleHit.rule_version == settings.DEFAULT_RULE_VERSION)
            .order_by(FactRuleHit.created_at.asc())
        )
        rows = (await session.execute(stmt)).scalars().all()
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            payload = cls._serialize_model(row)
            entity_id = str(payload["entity_id"])
            grouped.setdefault(entity_id, []).append(payload)
        return grouped

    @staticmethod
    def _serialize_model(instance: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for column in instance.__table__.columns:
            payload[column.name] = ResearchContextLoader._serialize_scalar(
                getattr(instance, column.name)
            )
        return payload

    @staticmethod
    def _serialize_scalar(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return value
