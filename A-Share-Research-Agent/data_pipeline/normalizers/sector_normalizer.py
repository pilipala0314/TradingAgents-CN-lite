from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

import pandas as pd

from core.enums import SectorType

from .common import first_existing

logger = logging.getLogger(__name__)


class SectorNormalizer:
    """板块主数据与成分映射标准化。"""

    @staticmethod
    def from_board_list(raw_df: pd.DataFrame, sector_type: str, source: str) -> pd.DataFrame:
        if raw_df.empty:
            return pd.DataFrame(
                columns=["sector_code", "sector_name", "sector_type", "parent_sector_id", "source", "is_active"]
            )

        records: list[dict[str, Any]] = []
        for raw_record in raw_df.to_dict(orient="records"):
            sector_name = str(
                first_existing(raw_record, "板块名称", "名称", "name", default="")
            ).strip()
            sector_code = str(
                first_existing(raw_record, "板块代码", "代码", "代码-", "symbol", default=sector_name)
            ).strip()
            if not sector_name:
                continue

            records.append(
                {
                    "sector_code": sector_code or sector_name,
                    "sector_name": sector_name,
                    "sector_type": sector_type,
                    "parent_sector_id": None,
                    "source": source,
                    "is_active": True,
                }
            )

        return pd.DataFrame(records).drop_duplicates(
            subset=["sector_code", "source"],
            keep="last",
        )

    @staticmethod
    def build_mapping(
        component_df: pd.DataFrame,
        *,
        security_map: dict[str, UUID],
        sector_id: UUID,
        business_date: date,
        relation_type: str = "member",
    ) -> pd.DataFrame:
        if component_df.empty:
            return pd.DataFrame(
                columns=[
                    "security_id",
                    "sector_id",
                    "relation_type",
                    "concept_purity_score",
                    "effective_from",
                    "effective_to",
                ]
            )

        records: list[dict[str, Any]] = []
        for raw_record in component_df.to_dict(orient="records"):
            symbol = str(first_existing(raw_record, "代码", "symbol", default="")).strip()
            security_id = security_map.get(symbol)
            if security_id is None:
                logger.debug("板块成分股未找到 security_id, symbol=%s", symbol)
                continue

            purity = first_existing(raw_record, "概念涨跌幅", "涨跌幅", "purity_score", default=None)
            records.append(
                {
                    "security_id": security_id,
                    "sector_id": sector_id,
                    "relation_type": relation_type,
                    "concept_purity_score": None if purity is None else pd.to_numeric(purity, errors="coerce"),
                    "effective_from": business_date,
                    "effective_to": None,
                }
            )

        return pd.DataFrame(records).drop_duplicates(
            subset=["security_id", "sector_id", "effective_from"],
            keep="last",
        )

    @staticmethod
    def normalize_sector_type(raw_value: str) -> str:
        value = raw_value.strip().lower()
        if value in {SectorType.INDUSTRY.value, "industry"}:
            return SectorType.INDUSTRY.value
        return SectorType.CONCEPT.value
