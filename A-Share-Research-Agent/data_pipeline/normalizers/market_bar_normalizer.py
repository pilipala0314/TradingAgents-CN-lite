from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import pandas as pd

from .common import first_existing, to_date, to_decimal

logger = logging.getLogger(__name__)


class MarketBarNormalizer:
    """将 AkShare 历史 K 线统一为 `fact_market_bar` 标准字段。"""

    @staticmethod
    def from_history(
        raw_df: pd.DataFrame,
        security_id: UUID,
        source: str,
        bar_timeframe: str,
        adj_type: str = "none",
        version: int = 1,
    ) -> pd.DataFrame:
        if raw_df.empty:
            return pd.DataFrame(
                columns=[
                    "security_id",
                    "bar_date",
                    "bar_timeframe",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "turnover",
                    "turnover_rate",
                    "adj_type",
                    "source",
                    "version",
                ]
            )

        records: list[dict[str, Any]] = []
        for raw_record in raw_df.to_dict(orient="records"):
            bar_date = to_date(first_existing(raw_record, "日期", "时间", "datetime", default=None))
            if bar_date is None:
                logger.debug("跳过缺少时间字段的 K 线记录: %s", raw_record)
                continue

            records.append(
                {
                    "security_id": security_id,
                    "bar_date": bar_date,
                    "bar_timeframe": bar_timeframe,
                    "open": to_decimal(first_existing(raw_record, "开盘", "open", default=0)),
                    "high": to_decimal(first_existing(raw_record, "最高", "high", default=0)),
                    "low": to_decimal(first_existing(raw_record, "最低", "low", default=0)),
                    "close": to_decimal(first_existing(raw_record, "收盘", "close", default=0)),
                    "volume": to_decimal(first_existing(raw_record, "成交量", "volume")),
                    "turnover": to_decimal(first_existing(raw_record, "成交额", "amount", "turnover")),
                    "turnover_rate": to_decimal(
                        first_existing(raw_record, "换手率", "turnover_rate")
                    ),
                    "adj_type": adj_type,
                    "source": source,
                    "version": version,
                }
            )

        return pd.DataFrame(records).drop_duplicates(
            subset=["security_id", "bar_date", "bar_timeframe", "adj_type", "version"],
            keep="last",
        )
