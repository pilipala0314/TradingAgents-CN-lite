"""板块层：东财 slist 个股板块归属 + 行业板块涨跌排名。

> 注：V3.2.2 之后 a-stock-data 已弃用百度 PAE getrelatedblock，改用东财 slist。
"""

from __future__ import annotations

import logging
from typing import Any

from ._shared import UA, em_get, em_market_code, normalize_ticker

logger = logging.getLogger(__name__)


def fetch_concept_blocks(code: str) -> dict[str, Any]:
    """拉取个股所属板块 / 概念 / 地域归属（一次请求拿全）。

    返回: {total, boards: [{name, code, change_pct, lead_stock}], concept_tags}
    boards 混合 行业/概念/地域，板块名自解释（如「食品饮料」= 行业，「贵州板块」= 地域）。
    """
    code = normalize_ticker(code)
    market_code = em_market_code(code)
    params = {
        "fltt": "2",
        "invt": "2",
        "secid": f"{market_code}.{code}",
        "spt": "3",
        "pi": "0",
        "pz": "200",
        "po": "1",
        "fields": "f12,f14,f3,f128",
    }
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    try:
        r = em_get(
            "https://push2.eastmoney.com/api/qt/slist/get",
            params=params,
            headers=headers,
            timeout=15,
        )
        d = r.json()
    except Exception as exc:
        logger.warning("eastmoney_concept_blocks(%s) failed: %s", code, exc)
        return {"total": 0, "boards": [], "concept_tags": []}

    diff = (d.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    boards: list[dict[str, Any]] = []
    for it in items:
        boards.append(
            {
                "name": it.get("f14", ""),
                "code": it.get("f12", ""),
                "change_pct": it.get("f3", ""),
                "lead_stock": it.get("f128", ""),
            }
        )
    return {
        "total": len(boards),
        "boards": boards,
        "concept_tags": [b["name"] for b in boards],
    }


def fetch_industry_ranking(limit: int = 90) -> list[dict[str, Any]]:
    """东财行业板块涨跌排名（V3.0 之后用 push2 his 替代同花顺）。

    返回: [{board_code, board_name, change_pct, lead_stock, lead_change_pct}, ...]
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": str(limit),
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90+t:2+f:!50",  # m:90 = 行业；t:2 = 板块；f:!50 = 排除东方财富板块
        "fields": "f1,f2,f3,f4,f12,f14,f128",
    }
    headers = {"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"}
    try:
        r = em_get(url, params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as exc:
        logger.warning("eastmoney_industry_ranking failed: %s", exc)
        return []
    diff = (d.get("data") or {}).get("diff") or []
    rows: list[dict[str, Any]] = []
    for it in diff:
        rows.append(
            {
                "board_code": it.get("f12", ""),
                "board_name": it.get("f14", ""),
                "change_pct": it.get("f3", 0.0),
                "lead_stock": it.get("f128", ""),
            }
        )
    return rows
