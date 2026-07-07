"""test_financial_report_provider — 财务三表 provider 单测。

覆盖：
- FinancialReportProvider 3 个端点签名一致 + 返回 DataFrame
- DataRouter EXTENDED_METHOD_TABLE 路由 3 端点到 financial_report
- 与 AkshareProvider 接口完全脱钩（akshare 财务 API 损坏）
- 失败语义：下游 raise → 透传（不静默吞）

注：单测 mock 掉 financials.* 三个函数，避免真网路依赖。verify_financial_reports.py
是真网路 e2e 验证脚本（不在 pytest 里跑）。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from data_pipeline.data_router import DataRouter
from data_pipeline.providers.financial_report_provider import FinancialReportProvider


# ── 基础结构 ──────────────────────────────────────────────────────
class TestFinancialReportProviderStructure:
    def test_has_three_methods(self):
        p = FinancialReportProvider()
        assert callable(getattr(p, "get_balance_sheet", None))
        assert callable(getattr(p, "get_cashflow_statement", None))
        assert callable(getattr(p, "get_income_statement", None))

    def test_methods_are_coroutines(self):
        import inspect
        p = FinancialReportProvider()
        assert inspect.iscoroutinefunction(p.get_balance_sheet)
        assert inspect.iscoroutinefunction(p.get_cashflow_statement)
        assert inspect.iscoroutinefunction(p.get_income_statement)


# ── 端点签名（与 AkshareProvider 旧实现一致）──────────────────
class TestSignature:
    @pytest.mark.asyncio
    async def test_balance_sheet_returns_dataframe(self):
        fake_rows = [
            {
                "SECUCODE": "600519.SH", "SECURITY_CODE": "600519",
                "REPORT_DATE": "2026-03-31 00:00:00", "TOTAL_ASSETS": 3.2e11,
            },
        ]
        with patch(
            "data_pipeline.providers.financial_report_provider.fetch_balance_sheet",
            return_value=fake_rows,
        ) as mock:
            p = FinancialReportProvider()
            r = await p.get_balance_sheet("600519")
            assert isinstance(r, pd.DataFrame)
            assert len(r) == 1
            assert r.iloc[0]["SECURITY_CODE"] == "600519"
            mock.assert_called_once_with("600519")

    @pytest.mark.asyncio
    async def test_cashflow_returns_dataframe(self):
        fake_rows = [
            {
                "SECUCODE": "000001.SZ", "SECURITY_CODE": "000001",
                "REPORT_DATE": "2026-03-31 00:00:00",
                "NETCASH_OPERATE": 1.5e10,
            },
        ]
        with patch(
            "data_pipeline.providers.financial_report_provider.fetch_cashflow_statement",
            return_value=fake_rows,
        ) as mock:
            p = FinancialReportProvider()
            r = await p.get_cashflow_statement("000001")
            assert isinstance(r, pd.DataFrame)
            assert len(r) == 1
            assert r.iloc[0]["NETCASH_OPERATE"] == 1.5e10
            mock.assert_called_once_with("000001")

    @pytest.mark.asyncio
    async def test_income_statement_returns_dataframe(self):
        fake_rows = [
            {
                "SECUCODE": "600519.SH", "SECURITY_CODE": "600519",
                "REPORT_DATE": "2026-03-31 00:00:00",
                "REVENUE": 5.0e10, "NETPROFIT": 2.0e10,
            },
        ]
        with patch(
            "data_pipeline.providers.financial_report_provider.fetch_income_statement",
            return_value=fake_rows,
        ) as mock:
            p = FinancialReportProvider()
            r = await p.get_income_statement("600519")
            assert isinstance(r, pd.DataFrame)
            assert len(r) == 1
            assert r.iloc[0]["REVENUE"] == 5.0e10
            mock.assert_called_once_with("600519")


# ── 失败语义：下游 raise → 透传 ─────────────────────────────
class TestFailureSemantics:
    @pytest.mark.asyncio
    async def test_balance_sheet_propagates_exception(self):
        with patch(
            "data_pipeline.providers.financial_report_provider.fetch_balance_sheet",
            side_effect=RuntimeError("东财数据中心 timeout"),
        ):
            p = FinancialReportProvider()
            with pytest.raises(RuntimeError, match="timeout"):
                await p.get_balance_sheet("600519")

    @pytest.mark.asyncio
    async def test_cashflow_propagates_exception(self):
        with patch(
            "data_pipeline.providers.financial_report_provider.fetch_cashflow_statement",
            side_effect=ConnectionError("network"),
        ):
            p = FinancialReportProvider()
            with pytest.raises(ConnectionError, match="network"):
                await p.get_cashflow_statement("600519")

    @pytest.mark.asyncio
    async def test_income_propagates_exception(self):
        with patch(
            "data_pipeline.providers.financial_report_provider.fetch_income_statement",
            side_effect=ValueError("bad data"),
        ):
            p = FinancialReportProvider()
            with pytest.raises(ValueError, match="bad data"):
                await p.get_income_statement("600519")


# ── DataRouter 集成 ───────────────────────────────────────────
class TestDataRouterIntegration:
    def test_extended_method_table_routes_to_financial_report(self):
        """P1：财务三表 3 端点路由到 financial_report provider（不再走 akshare）。"""
        from data_pipeline.data_router import DataRouter
        table = DataRouter.EXTENDED_METHOD_TABLE
        for m in ("get_balance_sheet", "get_cashflow_statement", "get_income_statement"):
            assert m in table, f"{m} 不在 EXTENDED_METHOD_TABLE"
            providers = table[m]
            assert "financial_report" in providers, (
                f"{m} 路由应包含 'financial_report'，实际：{providers}"
            )
            assert "akshare" not in providers, (
                f"{m} 不应再路由到 akshare（akshare 1.18.64 财务 API 损坏）"
            )

    def test_financial_report_in_default_providers(self):
        """P1：financial_report provider 注入 default providers dict。"""
        from data_pipeline.data_router import DataRouter
        r = DataRouter(include_extended=True)
        assert "financial_report" in r.providers
        assert isinstance(r.providers["financial_report"], FinancialReportProvider)
