"""ETL 子包共享的 session-aware 查询 helper。"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.dimensions import DimSector, DimSecurity, MapSecuritySector


async def load_security_rows(
    session: AsyncSession,
    *,
    symbols: list[str] | None = None,
) -> list[tuple[str, UUID, str]]:
    """拉取证券主数据 (symbol, security_id, exchange)。"""
    query = select(DimSecurity.symbol, DimSecurity.security_id, DimSecurity.exchange).where(
        DimSecurity.is_active.is_(True)
    )
    if symbols:
        query = query.where(DimSecurity.symbol.in_(symbols))
    result = await session.execute(query)
    return [(row[0], row[1], row[2]) for row in result.all()]


async def load_security_map(
    session: AsyncSession,
    *,
    symbols: list[str] | None = None,
) -> dict[str, UUID]:
    rows = await load_security_rows(session, symbols=symbols)
    return {symbol: security_id for symbol, security_id, _ in rows}


async def load_sector_rows(
    session: AsyncSession,
    *,
    sector_types: list[str] | None = None,
) -> list[tuple[str, str, UUID, str]]:
    """拉取板块主数据 (sector_code, sector_name, sector_id, sector_type)。"""
    query = select(
        DimSector.sector_code,
        DimSector.sector_name,
        DimSector.sector_id,
        DimSector.sector_type,
    ).where(DimSector.is_active.is_(True))
    if sector_types:
        query = query.where(DimSector.sector_type.in_(sector_types))
    result = await session.execute(query)
    return [(row[0], row[1], row[2], row[3]) for row in result.all()]


async def load_sector_map(session: AsyncSession) -> dict[str, UUID]:
    rows = await load_sector_rows(session)
    mapping: dict[str, UUID] = {}
    for sector_code, sector_name, sector_id, _ in rows:
        mapping[sector_code] = sector_id
        mapping[sector_name] = sector_id
    return mapping


async def load_sector_security_mapping(
    session: AsyncSession,
    business_date: date,
) -> list[tuple[UUID, UUID]]:
    """拉取当前生效的 security-sector 映射 (security_id, sector_id)。"""
    query = (
        select(MapSecuritySector.security_id, MapSecuritySector.sector_id)
        .where(MapSecuritySector.effective_from <= business_date)
        .where(
            or_(
                MapSecuritySector.effective_to.is_(None),
                MapSecuritySector.effective_to >= business_date,
            )
        )
    )
    result = await session.execute(query)
    return [(row[0], row[1]) for row in result.all()]
