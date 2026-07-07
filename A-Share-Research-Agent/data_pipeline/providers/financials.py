"""财报三表 — `RPT_DMSK_FN_BALANCE` / `RPT_DMSK_FN_CASHFLOW` / `RPT_DMSK_FN_INCOME`（东财数据中心）。

每个表函数返回 list[dict] — 一行 = 一份"期末快照"的"项目明细"，通常 30~80 条 KPI。

设计目标：
- 三表分得开（balance / cashflow / income），便于 Agent 按需调用。
- 统一接 secid，不依赖具体公司名。
"""
from __future__ import annotations

import logging
from typing import Any

from data_pipeline.providers._shared import eastmoney_datacenter, normalize_ticker

logger = logging.getLogger(__name__)


def _fetch_finance_table(
    code: str,
    report_name: str,
    period: str | None = None,
) -> list[dict[str, Any]]:
    """三表通用拉取：按 SECURITY_CODE 过滤，返回 `ALL` 列。"""
    code = normalize_ticker(code)
    if not code:
        raise ValueError("code 必填")
    filter_str = f'(SECURITY_CODE="{code}")'
    if period:
        filter_str += f'(REPORT_DATE=\'{period}\')'
    rows = eastmoney_datacenter(
        report_name=report_name,
        columns="ALL",
        filter_str=filter_str,
        page_size=200,
        sort_columns="REPORT_DATE",
        sort_types="-1",
    )
    if not rows:
        raise RuntimeError(f"东财 {report_name} 接口未返回数据, code={code}, period={period}")
    return rows


def fetch_balance_sheet(code: str, period: str | None = None) -> list[dict[str, Any]]:
    """资产负债表。"""
    rows = _fetch_finance_table(code, "RPT_DMSK_FN_BALANCE", period=period)
    logger.info("资产负债表拉取完成, code=%s, count=%d", code, len(rows))
    return rows


def fetch_cashflow_statement(code: str, period: str | None = None) -> list[dict[str, Any]]:
    """现金流量表。"""
    rows = _fetch_finance_table(code, "RPT_DMSK_FN_CASHFLOW", period=period)
    logger.info("现金流量表拉取完成, code=%s, count=%d", code, len(rows))
    return rows


def fetch_income_statement(code: str, period: str | None = None) -> list[dict[str, Any]]:
    """利润表。"""
    rows = _fetch_finance_table(code, "RPT_DMSK_FN_INCOME", period=period)
    logger.info("利润表拉取完成, code=%s, count=%d", code, len(rows))
    return rows
