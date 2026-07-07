"""test_financial_report_etl — P2-T3 财务报表 ETL 单测。

覆盖：
- _flatten_indicators: 宽表 → 长表
- _flatten_indicators: 空表 → 空 schema
- _flatten_indicators: 部分指标列缺失 → 跳过
- normalize_indicator_payload: 多种输入类型
- FinancialReportETL.run_indicators: 5 行写入
- FinancialReportETL.run_indicators: provider 空 → 0/0
- FinancialReportETL.run_indicators: provider 抛错 → 透传
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pandas as pd
import pytest

from data_pipeline.etl import financial_report_etl as fre_mod
from data_pipeline.etl.financial_report_etl import (
    FINANCIAL_SOURCE_TAG,
    FinancialReportETL,
)
from data_pipeline.providers.financial_report_provider import (
    _flatten_indicators,
    normalize_indicator_payload,
)
from models.facts import FactFinancialReport


class TestFlattenIndicators:
    def test_wide_to_long(self) -> None:
        raw = pd.DataFrame({
            "日期": ["2024-12-31", "2024-09-30", "2024-06-30"],
            "基本每股收益": [10.0, 8.0, 5.0],
            "每股净资产": [50.0, 48.0, 45.0],
            "加权平均净资产收益率(%)": [25.5, 20.0, 15.5],
            "净利润": [50_000_000_000.0, 40_000_000_000.0, 25_000_000_000.0],
        })
        out = _flatten_indicators(raw, source="x")
        # 4 个指标 × 3 报告期 = 12 行
        assert len(out) == 12
        # 期望列
        assert set(out.columns) == {"report_period", "indicator_code", "indicator_name", "value", "unit", "source"}
        # 各 indicator 都出现
        codes = set(out["indicator_code"])
        assert {"EPS", "BVPS", "ROE", "NET_PROFIT"} <= codes
        # 日期排序
        periods = sorted(set(out["report_period"]))
        assert periods[0] == date(2024, 6, 30)
        assert periods[-1] == date(2024, 12, 31)

    def test_empty_input(self) -> None:
        out = _flatten_indicators(pd.DataFrame(), source="x")
        assert out.empty
        assert "indicator_code" in out.columns

    def test_missing_columns_skip(self) -> None:
        """指标列缺失 → 跳过该指标。"""
        raw = pd.DataFrame({
            "日期": ["2024-12-31"],
            "基本每股收益": [10.0],
            # 缺少 BVPS / ROE / NET_PROFIT
        })
        out = _flatten_indicators(raw, source="x")
        assert len(out) == 1
        assert out["indicator_code"].iloc[0] == "EPS"

    def test_invalid_dates_dropped(self) -> None:
        """日期无效 → dropna(report_period) 丢弃。"""
        raw = pd.DataFrame({
            "日期": ["2024-12-31", "invalid"],
            "基本每股收益": [10.0, 5.0],
        })
        out = _flatten_indicators(raw, source="x")
        assert len(out) == 1
        assert out["report_period"].iloc[0] == date(2024, 12, 31)


class TestNormalizePayload:
    def test_none_returns_empty(self) -> None:
        out = normalize_indicator_payload(None)
        assert out.empty

    def test_list_of_dict(self) -> None:
        out = normalize_indicator_payload([{
            "日期": "2024-12-31",
            "基本每股收益": 10.0,
        }])
        assert len(out) == 1
        assert out["indicator_code"].iloc[0] == "EPS"

    def test_dataframe(self) -> None:
        df = pd.DataFrame({
            "日期": ["2024-12-31"],
            "基本每股收益": [10.0],
        })
        out = normalize_indicator_payload(df)
        assert len(out) == 1

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError):
            normalize_indicator_payload("not a dataframe")


def _stub_fre_module(rows: list | None = None, upsert_mock: AsyncMock | None = None):
    from unittest.mock import patch as _patch

    @contextmanager
    def _ctx():
        with _patch.object(fre_mod.OpsAuditService, "start_job", new=AsyncMock(return_value="job-1")):
            with _patch.object(fre_mod.OpsAuditService, "finish_job_success", new=AsyncMock()):
                with _patch.object(fre_mod.OpsAuditService, "finish_job_failed", new=AsyncMock()):
                    with _patch.object(fre_mod, "upsert_dataframe", new=upsert_mock or AsyncMock(return_value=5)):
                        yield
    return _ctx


class TestFinancialReportETL:
    @pytest.mark.asyncio
    async def test_run_indicators_writes_long_table(self) -> None:
        sec_id = uuid4()
        long_df = pd.DataFrame({
            "report_period": [date(2024, 12, 31), date(2024, 9, 30)],
            "indicator_code": ["EPS", "ROE"],
            "indicator_name": ["earnings_per_share", "weighted_roe"],
            "value": [10.0, 20.0],
            "unit": ["yuan", "pct"],
            "source": [FINANCIAL_SOURCE_TAG] * 2,
        })
        provider = MagicMock()
        provider.get_indicators = AsyncMock(return_value=long_df)

        upsert_mock = AsyncMock(return_value=2)
        with _stub_fre_module(upsert_mock=upsert_mock)():
            etl = FinancialReportETL(provider)
            out = await etl.run_indicators(
                MagicMock(), business_date=date(2025, 7, 7),
                security_id=sec_id, symbol="600519",
            )
        assert out["synced_count"] == 2
        # 验证 long_df 已被补 security_id + version
        call_args = upsert_mock.await_args
        df_arg = call_args.kwargs["df"]
        assert (df_arg["security_id"] == sec_id).all()
        assert (df_arg["version"] == 1).all()
        assert call_args.kwargs["model"] is FactFinancialReport

    @pytest.mark.asyncio
    async def test_run_indicators_empty_returns_0(self) -> None:
        sec_id = uuid4()
        provider = MagicMock()
        provider.get_indicators = AsyncMock(return_value=pd.DataFrame())

        with _stub_fre_module(upsert_mock=AsyncMock())() as _:
            etl = FinancialReportETL(provider)
            out = await etl.run_indicators(
                MagicMock(), business_date=date(2025, 7, 7),
                security_id=sec_id, symbol="600519",
            )
        assert out["synced_count"] == 0
        assert out["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_run_indicators_provider_error_propagates(self) -> None:
        sec_id = uuid4()
        provider = MagicMock()
        provider.get_indicators = AsyncMock(side_effect=RuntimeError("akshare net error"))

        with _stub_fre_module(upsert_mock=AsyncMock())():
            etl = FinancialReportETL(provider)
            with pytest.raises(RuntimeError, match="akshare net error"):
                await etl.run_indicators(
                    MagicMock(), business_date=date(2025, 7, 7),
                    security_id=sec_id, symbol="600519",
                )
