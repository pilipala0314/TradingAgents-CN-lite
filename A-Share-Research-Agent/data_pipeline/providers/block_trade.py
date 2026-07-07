"""大宗交易 — `RPT_BLOCKTRADE`（东财数据中心）。

返回结构: list[{date, code, name, price, amount_yi, direction, buyer, seller}, ...]
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from data_pipeline.providers._shared import eastmoney_datacenter, normalize_ticker

logger = logging.getLogger(__name__)


def fetch_block_trade(code: str, recent_days: int = 30) -> list[dict[str, Any]]:
    """大宗交易明细（最近 N 日）。

    接口: `RPT_BLOCKTRADE`，按代码 + 日期范围过滤。
    """
    code = normalize_ticker(code)
    if not code:
        raise ValueError("code 必填")
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=max(1, recent_days))).strftime("%Y-%m-%d")
    rows = eastmoney_datacenter(
        report_name="RPT_BLOCKTRADE",
        columns="ALL",
        filter_str=(
            f'(SECURITY_CODE="{code}")'
            f'(TRADE_DATE>=\'{start_date}\')'
            f'(TRADE_DATE<=\'{end_date}\')'
        ),
        page_size=200,
        sort_columns="TRADE_DATE",
        sort_types="-1",
    )
    if not rows:
        raise RuntimeError(f"东财大宗交易接口未返回数据, code={code}, range={start_date}..{end_date}")
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "date": row.get("TRADE_DATE"),
                "code": row.get("SECURITY_CODE"),
                "name": row.get("SECURITY_NAME_ABBR"),
                "price": row.get("PRICE"),
                "amount_yi": row.get("DEAL_AMT"),
                "direction": row.get("TRADE_DIRECTION"),
                "buyer": row.get("BUYER_NAME"),
                "seller": row.get("SELLER_NAME"),
                "discount_pct": row.get("CLOSE_PRICE"),
            }
        )
    logger.info("大宗交易拉取完成, code=%s, count=%d", code, len(results))
    return results
