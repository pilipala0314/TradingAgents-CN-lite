from __future__ import annotations

import logging

import pandas as pd

from core.enums import SecurityType

from .common import first_existing, infer_exchange

logger = logging.getLogger(__name__)


class SecurityNormalizer:
    """将行情列表标准化为 `dim_security` 可入库记录。"""

    @staticmethod
    def from_spot_list(stock_df: pd.DataFrame) -> pd.DataFrame:
        if stock_df.empty:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "raw_symbol",
                    "security_type",
                    "exchange",
                    "name",
                    "list_date",
                    "delist_date",
                    "is_active",
                ]
            )

        records: list[dict[str, object]] = []
        for raw_record in stock_df.to_dict(orient="records"):
            symbol = str(first_existing(raw_record, "代码", "symbol", default="")).strip()
            name = str(first_existing(raw_record, "名称", "name", default="")).strip()
            if not symbol or not name:
                logger.debug("跳过缺少代码或名称的股票记录: %s", raw_record)
                continue

            records.append(
                {
                    "symbol": symbol,
                    "raw_symbol": symbol,
                    "security_type": SecurityType.STOCK.value,
                    "exchange": infer_exchange(symbol),
                    "name": name,
                    "list_date": None,
                    "delist_date": None,
                    "is_active": True,
                }
            )

        return pd.DataFrame(records).drop_duplicates(subset=["symbol"], keep="last")
