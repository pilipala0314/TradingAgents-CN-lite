"""财报三表 Provider — 东财数据中心 RPT_DMSK_FN_BALANCE / CASHFLOW / INCOME（HTTP JSON）。

为什么不用 akshare？
- akshare 1.18.64 的 `stock_balance_sheet_by_report_em` / `*_yearly_em` 上游损坏
  (页面 `hidctype` 解析失败：`soup.find(attrs={"id": "hidctype"})` 返 None → 抛 TypeError)
- akshare 1.18.64 也没有 `stock_*_by_quarterly_em` 资产负债表变体
- 但 `stock_cash_flow_sheet_by_quarterly_em` 和 `stock_profit_sheet_by_quarterly_em` 是好的
  （部分端点仍可用，但不足以凑齐"三表"）

兜底路径：东财数据中心 `datacenter-web.eastmoney.com/api/data/v1/get` 三表 RPT
- 数据源稳定 (cookie / 风控无)
- 返回结构：list[dict]，一行 = 一份"期末快照"+"项目明细"
- 通常 90~120 条 / 报表 / 股票（含历史多年）

端点（DataRouter 通过 'financial_report' provider 名路由）：
- `get_balance_sheet(symbol) -> pd.DataFrame`
- `get_cashflow_statement(symbol) -> pd.DataFrame`
- `get_income_statement(symbol) -> pd.DataFrame`

返回 pd.DataFrame（与 AkshareProvider 一致），由 facade_normalizer 归一化。
"""
from __future__ import annotations

import asyncio
import logging

import pandas as pd

from data_pipeline.providers.financials import (
    fetch_balance_sheet,
    fetch_cashflow_statement,
    fetch_income_statement,
)

logger = logging.getLogger(__name__)


class FinancialReportProvider:
    """财报三表 Provider — DataRouter 的 'financial_report' provider。

    实现走东财数据中心，**不依赖 akshare 财务接口**（akshare 1.18.64 财务 API 损坏）。
    """

    async def get_balance_sheet(self, symbol: str) -> pd.DataFrame:
        """资产负债表（东财 RPT_DMSK_FN_BALANCE）。"""
        logger.info("FinancialReportProvider 拉取资产负债表, symbol=%s", symbol)
        rows = await asyncio.to_thread(fetch_balance_sheet, symbol)
        return pd.DataFrame(rows)

    async def get_cashflow_statement(self, symbol: str) -> pd.DataFrame:
        """现金流量表（东财 RPT_DMSK_FN_CASHFLOW）。"""
        logger.info("FinancialReportProvider 拉取现金流量表, symbol=%s", symbol)
        rows = await asyncio.to_thread(fetch_cashflow_statement, symbol)
        return pd.DataFrame(rows)

    async def get_income_statement(self, symbol: str) -> pd.DataFrame:
        """利润表（东财 RPT_DMSK_FN_INCOME）。"""
        logger.info("FinancialReportProvider 拉取利润表, symbol=%s", symbol)
        rows = await asyncio.to_thread(fetch_income_statement, symbol)
        return pd.DataFrame(rows)
