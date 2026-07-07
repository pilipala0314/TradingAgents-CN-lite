"""涨停揭秘 — `RPT_LIMIT_REMOVE` / `RPT_ZTGG`（东财数据中心）。

返回结构: list[{date, code, name, change_pct, amount, zt_reason, consecutive_boards}, ...]
- zt_reason: 涨停原因
- consecutive_boards: 连板数
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from data_pipeline.providers._shared import eastmoney_datacenter, normalize_ticker

logger = logging.getLogger(__name__)


def fetch_zt_unlock(code: str, recent_days: int = 30) -> list[dict[str, Any]]:
    """涨停揭秘明细（最近 N 日）。"""
    code = normalize_ticker(code)
    if not code:
        raise ValueError("code 必填")
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=max(1, recent_days))).strftime("%Y-%m-%d")
    rows = eastmoney_datacenter(
        report_name="RPT_ZTGG",
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
        raise RuntimeError(
            f"东财涨停揭秘接口未返回数据, code={code}, range={start_date}..{end_date}"
        )
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "date": row.get("TRADE_DATE"),
                "code": row.get("SECURITY_CODE"),
                "name": row.get("SECURITY_NAME_ABBR"),
                "change_pct": row.get("CHANGE_RATE"),
                "amount": row.get("TURNOVER"),
                "zt_reason": row.get("LIMIT_REASON"),
                "consecutive_boards": row.get("LIMIT_TIMES"),
            }
        )
    logger.info("涨停揭秘拉取完成, code=%s, count=%d", code, len(results))
    return results
