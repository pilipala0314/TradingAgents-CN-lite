"""打板情绪 — 涨停 / 跌停 / 炸板 全市场池聚合。

数据源: 东财 `ak.stock_zt_pool_em` / `ak.stock_zt_pool_dtgc_em` / `ak.stock_zt_pool_zbgc_em`
走 `AkShareClient` 薄壳。

返回结构: dict{
    "date": str,
    "zt_count": int, "dt_count": int, "bomb_count": int,
    "zt_amount_total": float, "first_zt_median_change": float,
    "max_consecutive_boards": int, "limit_up_break_rate": float
}
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime
from typing import Any

import pandas as pd

from data_pipeline.akshare_client import AkShareClient

logger = logging.getLogger(__name__)


def _df_change_pct(df: pd.DataFrame) -> list[float]:
    if df is None or df.empty:
        return []
    for col in ("涨跌幅", "change_pct", "pct_change"):
        if col in df.columns:
            return pd.to_numeric(df[col], errors="coerce").fillna(0.0).tolist()
    return []


async def fetch_auction_sentiment(business_date: str | None = None) -> dict[str, Any]:
    """全市场打板情绪聚合（指定业务日 YYYYMMDD，None=今日）。

    不允许降级：任一池接口失败就抛错，由调用方决定是否当日重试。
    """
    client = AkShareClient()
    target_date = business_date or datetime.now().strftime("%Y%m%d")

    zt_df = await client.get_limit_up_pool(date=target_date)
    dt_df = await client.get_limit_down_pool(date=target_date)
    bomb_df = await client.get_bomb_board_pool(date=target_date)

    zt_changes = _df_change_pct(zt_df)
    zt_amount = 0.0
    for col in ("成交额", "amount", "turnover"):
        if col in zt_df.columns:
            zt_amount = float(pd.to_numeric(zt_df[col], errors="coerce").fillna(0.0).sum())
            break

    max_boards = 0
    for col in ("连板数", "consecutive_boards", "limit_times"):
        if col in zt_df.columns:
            max_boards = int(pd.to_numeric(zt_df[col], errors="coerce").fillna(0).max())
            break

    zt_count = len(zt_df)
    dt_count = len(dt_df)
    bomb_count = len(bomb_df)
    first_zt_median = float(statistics.median(zt_changes)) if zt_changes else 0.0
    break_rate = (bomb_count / zt_count) if zt_count > 0 else 0.0

    sentiment = {
        "date": business_date or datetime.now().strftime("%Y-%m-%d"),
        "zt_count": zt_count,
        "dt_count": dt_count,
        "bomb_count": bomb_count,
        "zt_amount_total": zt_amount,
        "first_zt_median_change": first_zt_median,
        "max_consecutive_boards": max_boards,
        "limit_up_break_rate": break_rate,
    }
    logger.info("打板情绪聚合完成, %s", sentiment)
    return sentiment
