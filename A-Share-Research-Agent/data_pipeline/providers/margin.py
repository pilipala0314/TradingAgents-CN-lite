"""融资融券 — `RPT_MARGIN_DETAIL`（东财数据中心）。

返回结构: list[{date, rzye, rzmre, rqye, rqmcl, rzrqye}, ...]
- rzye: 融资余额（元）
- rzmre: 融资买入额（元）
- rqye: 融券余额（元）
- rqmcl: 融券卖出量（股）
- rzrqye: 融资融券余额（元）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from data_pipeline.providers._shared import eastmoney_datacenter, normalize_ticker

logger = logging.getLogger(__name__)


def fetch_margin_data(code: str, recent_days: int = 30) -> list[dict[str, Any]]:
    """融资融券日度数据（最近 N 日）。"""
    code = normalize_ticker(code)
    if not code:
        raise ValueError("code 必填")
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=max(1, recent_days))).strftime("%Y-%m-%d")
    rows = eastmoney_datacenter(
        report_name="RPT_MARGIN_DETAIL",
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
        raise RuntimeError(f"东财融资融券接口未返回数据, code={code}, range={start_date}..{end_date}")
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            {
                "date": row.get("TRADE_DATE"),
                "rzye": row.get("RZYE"),
                "rzmre": row.get("RZMRE"),
                "rqye": row.get("RQYE"),
                "rqmcl": row.get("RQMCL"),
                "rzrqye": row.get("RZRQYE"),
                "rzye_change_pct": row.get("RZYE_RATIO"),
            }
        )
    logger.info("融资融券拉取完成, code=%s, count=%d", code, len(results))
    return results
