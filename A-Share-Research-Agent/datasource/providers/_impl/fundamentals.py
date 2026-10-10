"""基础数据层：mootdx 财务快照 + 东财个股基本面。"""

from __future__ import annotations

import logging
from typing import Any

from datasource.providers._shared import UA, em_get, em_market_code, normalize_ticker, tdx_query
from datasource.providers._shared.utils import to_native

logger = logging.getLogger(__name__)


def fetch_finance_snapshot(code: str) -> dict[str, Any] | None:
    """mootdx 季报快照（37 字段，EPS/ROE/净利/主营收入）。

    返回: {eps, bvps, roe, profit, income, liutongguben, zongguben, ...}
    """
    code = normalize_ticker(code)
    try:
        df = tdx_query(lambda c: c.finance(symbol=code))
    except Exception as exc:
        logger.warning("mootdx finance(%s) failed: %s", code, exc)
        return None
    if df is None or len(df) == 0:
        return None
    row = df.iloc[0].to_dict()
    return {k: to_native(v) for k, v in row.items()}


def fetch_stock_info(code: str) -> dict[str, Any]:
    """东财个股基本面（行业 / 总股本 / 流通股 / 总市值 / 流通市值 / 上市日期）。

    返回: {code, name, industry, total_shares, float_shares, mcap, float_mcap, list_date, price}
    """
    code = normalize_ticker(code)
    market_code = em_market_code(code)
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
        "secid": f"{market_code}.{code}",
    }
    headers = {"User-Agent": UA}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        d = r.json().get("data", {}) or {}
    except Exception as exc:
        # 网络/解析错误 → raise（DataSource 重试/fallback），不静默返空 dict
        logger.warning("eastmoney_stock_info(%s) failed: %s", code, exc)
        raise RuntimeError(f"eastmoney_stock_info({code}) failed: {exc}") from exc
    if not d:
        raise RuntimeError(f"eastmoney_stock_info({code}): 响应缺少 data（可能被 WAF 拦截或代码无效）")
    return {
        "code": d.get("f57", ""),
        "name": d.get("f58", ""),
        "industry": d.get("f127", ""),
        "total_shares": d.get("f84", 0),
        "float_shares": d.get("f85", 0),
        "mcap": d.get("f116", 0),
        "float_mcap": d.get("f117", 0),
        "list_date": str(d.get("f189", "")),
        "price": d.get("f43", 0),
    }
