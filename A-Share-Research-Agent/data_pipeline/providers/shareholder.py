"""股东户数 — `RPT_F10_EH_PER`（东财数据中心）。

返回结构: list[{date, holder_count, change_pct, avg_market_cap_yi}, ...]
"""
from __future__ import annotations

import logging
from typing import Any

from data_pipeline.providers._shared import (
    eastmoney_datacenter,
    em_market_code,
    normalize_ticker,
)

logger = logging.getLogger(__name__)


def fetch_shareholder_count(code: str, recent_n: int = 8) -> list[dict[str, Any]]:
    """股东户数（最近 N 个报告期）。

    接口: `RPT_F10_EH_PER`（按 secid 过滤），按 `END_DATE` 降序，取最近 N。
    """
    code = normalize_ticker(code)
    if not code:
        raise ValueError("code 必填")
    secid = f"{em_market_code(code)}.{code}"
    rows = eastmoney_datacenter(
        report_name="RPT_F10_EH_PER",
        columns="ALL",
        filter_str=f'(SECUCODE="{code}")(SECURITY_CODE="{code}")',
        page_size=max(1, min(recent_n, 50)),
        sort_columns="END_DATE",
        sort_types="-1",
    )
    if not rows:
        raise RuntimeError(f"东财股东户数接口未返回数据, code={code}")
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "date": row.get("END_DATE"),
                "holder_count": row.get("HOLDER_NUM"),
                "change_pct": row.get("HOLDER_CHANGE_RATIO"),
                "avg_market_cap_yi": row.get("AVG_MARKET_CAP"),
                "avg_hold_shares": row.get("AVG_HOLD_NUM"),
                "secid": secid,
            }
        )
    logger.info("股东户数拉取完成, code=%s, count=%d", code, len(results))
    return results
