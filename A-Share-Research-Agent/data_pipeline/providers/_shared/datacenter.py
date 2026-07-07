"""东财数据中心统一查询（龙虎榜 / 解禁 / 融资融券 / 大宗交易 / 股东户数 / 分红 共用）。

提供 `eastmoney_datacenter(report_name, ...)` 单一入口，按 `reportName` 路由到对应数据集。
"""
from __future__ import annotations

from .http_client import em_get

DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
) -> list[dict]:
    """东财数据中心统一查询。

    返回数据列表（空表示无数据或接口失败）。
    """
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": "1",
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    r = em_get(DATACENTER_URL, params=params, timeout=15)
    d = r.json()
    if d.get("result") and d["result"].get("data"):
        return d["result"]["data"]
    return []
