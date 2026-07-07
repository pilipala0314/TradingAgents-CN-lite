"""test_sw_industry_kline_etl — P0-T4 申万行业指数 K 线 ETL 单测。"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pandas as pd
import pytest

from data_pipeline.etl.sw_industry_kline_etl import (
    SW_EXCHANGE,
    SW_SECURITY_TYPE,
    SWIndustryKlineETL,
    _build_sw_industry_security_df,
)

logger = logging.getLogger(__name__)


class _FakeSWIndustryProvider:
    def __init__(
        self,
        *,
        index_list: pd.DataFrame | None = None,
        kline_by_symbol: dict[str, pd.DataFrame] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        # 31 个申万一级行业指数（取前 3 个做测试）
        self._index_list = index_list if index_list is not None else pd.DataFrame({
            "code": ["801010", "801040", "801080"],
            "name": ["农林牧渔", "有色金属", "电子"],
        })
        self._kline_by_symbol = kline_by_symbol or {}
        self._fail_on = fail_on or set()

    async def get_index_list(self) -> pd.DataFrame:
        if "stock_list" in self._fail_on:
            raise RuntimeError("simulated akshare failure")
        return self._index_list.copy()

    async def get_daily_bars(
        self, symbol: str, start_date=None, end_date=None, adjust: str = "hfq",
    ) -> pd.DataFrame:
        if "kline" in self._fail_on or symbol in self._fail_on:
            raise RuntimeError(f"simulated kline failure: {symbol}")
        return self._kline_by_symbol.get(symbol, pd.DataFrame()).copy()


def _sw_kline(days: int = 5) -> pd.DataFrame:
    """构造申万行业指数 K 线（中文 key，对齐 akshare 真实返回）。"""
    dates = pd.date_range(end="2026-07-07", periods=days, freq="B")
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": [1500.0 + i for i in range(days)],
        "收盘": [1510.0 + i for i in range(days)],
        "最高": [1520.0 + i for i in range(days)],
        "最低": [1495.0 + i for i in range(days)],
        "成交量": [1_000_000 + i * 100_000 for i in range(days)],
        "成交额": [1_500_000_000.0 + i * 10_000_000 for i in range(days)],
        "振幅": [1.2] * days,
        "涨跌幅": [0.5, 0.3, -0.2, 0.8, 0.1][:days],
        "涨跌额": [10.0, 5.0, -3.0, 12.0, 1.5][:days],
        "换手率": [0.0] * days,  # 行业指数换手率为 0（无个股换手概念）
    })


def _mock_session() -> MagicMock:
    s = MagicMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    return s


class TestBuildSWIndustrySecurityDf:
    def test_empty_returns_schema(self) -> None:
        df = _build_sw_industry_security_df(pd.DataFrame())
        assert df.empty
        assert "symbol" in df.columns

    def test_normalizes_sw_industry_records(self) -> None:
        raw = pd.DataFrame({
            "code": ["801010", "801040", "801080"],
            "name": ["农林牧渔", "有色金属", "电子"],
        })
        df = _build_sw_industry_security_df(raw)
        assert len(df) == 3
        assert set(df["symbol"]) == {"801010", "801040", "801080"}
        assert all(df["security_type"] == SW_SECURITY_TYPE)
        assert all(df["exchange"] == SW_EXCHANGE)
        assert bool(df["is_active"].all())


class TestSWIndustryKlineETLSyncSecurities:
    @pytest.mark.asyncio
    async def test_sync_calls_upsert(self) -> None:
        provider = _FakeSWIndustryProvider()
        etl = SWIndustryKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch("data_pipeline.etl.sw_industry_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert, \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_security_sync(session, business_date=date(2026, 7, 7))  # type: ignore[arg-type]

        assert result["synced_count"] == 3
        call_args = mock_upsert.call_args
        df_arg = call_args.kwargs["df"]
        assert set(df_arg["symbol"]) == {"801010", "801040", "801080"}
        assert all(df_arg["security_type"] == "industry_index")
        assert all(df_arg["exchange"] == "SW")
        from models.dimensions import DimSecurity
        assert call_args.kwargs["model"] is DimSecurity

    @pytest.mark.asyncio
    async def test_sync_fails_loud(self) -> None:
        provider = _FakeSWIndustryProvider(fail_on={"stock_list"})
        etl = SWIndustryKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.finish_job_failed", new=AsyncMock()) as mock_failed:
            with pytest.raises(RuntimeError, match="simulated akshare failure"):
                await etl.run_security_sync(session, business_date=date(2026, 7, 7))  # type: ignore[arg-type]
        mock_failed.assert_awaited_once()


class TestSWIndustryKlineETLRunDailyBars:
    @pytest.mark.asyncio
    async def test_writes_fact_market_bar(self) -> None:
        sid_010 = uuid4()
        sid_040 = uuid4()
        sid_080 = uuid4()
        provider = _FakeSWIndustryProvider(
            kline_by_symbol={
                "801010": _sw_kline(5),
                "801040": _sw_kline(5),
                "801080": _sw_kline(5),
            },
        )
        etl = SWIndustryKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.sw_industry_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("801010", sid_010, "SW"),
                ("801040", sid_040, "SW"),
                ("801080", sid_080, "SW"),
            ]),
        ), patch("data_pipeline.etl.sw_industry_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert, \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
                symbols=["801010", "801040", "801080"],
            )

        assert result["synced_count"] == 15
        assert result["failure_count"] == 0
        call_args = mock_upsert.call_args
        df_arg = call_args.kwargs["df"]
        assert set(df_arg["security_id"].unique()) == {sid_010, sid_040, sid_080}
        assert all(df_arg["source"] == "akshare.stock_board_industry_hist_em")
        assert all(df_arg["adj_type"] == "hfq")  # 默认后复权
        from models.facts import FactMarketBar
        assert call_args.kwargs["model"] is FactMarketBar

    @pytest.mark.asyncio
    async def test_filters_non_sw(self) -> None:
        sid_sw = uuid4()
        sid_a = uuid4()
        provider = _FakeSWIndustryProvider(
            kline_by_symbol={"801010": _sw_kline(3)},
        )
        etl = SWIndustryKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.sw_industry_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("801010", sid_sw, "SW"),
                ("600519", sid_a, "sh"),  # A 股过滤
            ]),
        ), patch("data_pipeline.etl.sw_industry_kline_etl.upsert_dataframe", new=AsyncMock()), \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
            )
        assert result["synced_count"] == 3

    @pytest.mark.asyncio
    async def test_continues_on_per_symbol_failure(self) -> None:
        sid_010 = uuid4()
        sid_080 = uuid4()
        provider = _FakeSWIndustryProvider(
            kline_by_symbol={"801010": _sw_kline(3)},
            fail_on={"801080"},
        )
        etl = SWIndustryKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.sw_industry_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("801010", sid_010, "SW"),
                ("801080", sid_080, "SW"),
            ]),
        ), patch("data_pipeline.etl.sw_industry_kline_etl.upsert_dataframe", new=AsyncMock()), \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
            )
        assert result["synced_count"] == 3
        assert result["failure_count"] == 1
        assert result["failures"][0]["symbol"] == "801080"

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_sw_securities(self) -> None:
        provider = _FakeSWIndustryProvider()
        etl = SWIndustryKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.sw_industry_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[]),  # 完全没有记录
        ), patch("data_pipeline.etl.sw_industry_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert, \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.sw_industry_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
            )
        assert result["synced_count"] == 0
        assert result["failure_count"] == 0
        mock_upsert.assert_not_called()
