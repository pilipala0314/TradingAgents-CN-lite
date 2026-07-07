from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pandas as pd

from core.enums import EntityType, FlowType, WindowType
from core.identifiers import MARKET_ENTITY_ID

from .common import first_existing, to_decimal


class CapitalFlowNormalizer:
    """将不同来源的资金流数据统一到 `fact_capital_flow`。"""

    @staticmethod
    def market_main_force(
        raw_df: pd.DataFrame,
        business_date: date,
        source: str,
        version: int = 1,
    ) -> pd.DataFrame:
        if raw_df.empty:
            return pd.DataFrame()

        latest = raw_df.to_dict(orient="records")[-1]
        net_inflow = to_decimal(
            first_existing(latest, "今日主力净流入-净额", "主力净流入-净额", "净流入", default=0)
        )
        gross_inflow = to_decimal(first_existing(latest, "今日超大单净流入-净额", default=None))

        return pd.DataFrame(
            [
                {
                    "entity_type": EntityType.MARKET.value,
                    "entity_id": MARKET_ENTITY_ID,
                    "business_date": business_date,
                    "window_type": WindowType.CLOSE.value,
                    "flow_type": FlowType.MAIN_FORCE.value,
                    "net_inflow": net_inflow,
                    "gross_inflow": gross_inflow,
                    "gross_outflow": None,
                    "source": source,
                    "version": version,
                }
            ]
        )

    @staticmethod
    def northbound_summary(
        raw_df: pd.DataFrame,
        business_date: date,
        source: str,
        version: int = 1,
    ) -> pd.DataFrame:
        if raw_df.empty:
            return pd.DataFrame()

        record = raw_df.to_dict(orient="records")[-1]
        net_inflow = to_decimal(
            first_existing(record, "今日净流入", "当日净流入", "净买额", default=Decimal("0"))
        )
        gross_inflow = to_decimal(first_existing(record, "沪股通", "买入成交净额"))
        gross_outflow = to_decimal(first_existing(record, "深股通", "卖出成交净额"))

        return pd.DataFrame(
            [
                {
                    "entity_type": EntityType.MARKET.value,
                    "entity_id": MARKET_ENTITY_ID,
                    "business_date": business_date,
                    "window_type": WindowType.CLOSE.value,
                    "flow_type": FlowType.NORTHBOUND.value,
                    "net_inflow": net_inflow,
                    "gross_inflow": gross_inflow,
                    "gross_outflow": gross_outflow,
                    "source": source,
                    "version": version,
                }
            ]
        )

    @staticmethod
    def individual_main_force(
        raw_df: pd.DataFrame,
        security_id: UUID,
        business_date: date,
        source: str,
        version: int = 1,
    ) -> pd.DataFrame:
        if raw_df.empty:
            return pd.DataFrame()

        record = raw_df.to_dict(orient="records")[-1]
        return pd.DataFrame(
            [
                {
                    "entity_type": EntityType.SECURITY.value,
                    "entity_id": security_id,
                    "business_date": business_date,
                    "window_type": WindowType.CLOSE.value,
                    "flow_type": FlowType.INDIVIDUAL_MAIN_FORCE.value,
                    "net_inflow": to_decimal(
                        first_existing(record, "主力净流入-净额", "今日主力净流入-净额", "净流入")
                    ),
                    "gross_inflow": to_decimal(
                        first_existing(record, "超大单净流入-净额", "今日超大单净流入-净额")
                    ),
                    "gross_outflow": to_decimal(
                        first_existing(record, "大单净流入-净额", "今日大单净流入-净额")
                    ),
                    "source": source,
                    "version": version,
                }
            ]
        )

    @staticmethod
    def sector_main_force(
        *,
        sector_id: UUID,
        business_date: date,
        net_inflow: Decimal | float | int | None,
        gross_inflow: Decimal | float | int | None,
        gross_outflow: Decimal | float | int | None,
        source: str,
        version: int = 1,
    ) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "entity_type": EntityType.SECTOR.value,
                    "entity_id": sector_id,
                    "business_date": business_date,
                    "window_type": WindowType.CLOSE.value,
                    "flow_type": FlowType.SECTOR_MAIN_FORCE.value,
                    "net_inflow": to_decimal(net_inflow, default=Decimal("0")),
                    "gross_inflow": to_decimal(gross_inflow),
                    "gross_outflow": to_decimal(gross_outflow),
                    "source": source,
                    "version": version,
                }
            ]
        )
