"""test_us_kline_etl — P0-T2 美股 K 线 ETL 单测。

模式对齐 test_hk_kline_etl：
- `_FakeUSProvider` 注入固定数据
- mock `load_security_rows` / `upsert_dataframe` / `OpsAuditService`
- 覆盖数据归一 / upsert / 失败语义 / 过滤非 US 记录
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pandas as pd
import pytest

from data_pipeline.etl.us_kline_etl import (
    US_EXCHANGE,
    US_SECURITY_TYPE,
    USKlineETL,
    _build_us_security_df,
)

logger = logging.getLogger(__name__)


class _FakeUSProvider:
    def __init__(
        self,
        *,
        stock_list: pd.DataFrame | None = None,
        kline_by_symbol: dict[str, pd.DataFrame] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self._stock_list = stock_list if stock_list is not None else pd.DataFrame({
            "code": ["AAPL", "TSLA"],
            "name": ["Apple Inc.", "Tesla Inc."],
            "exchange": ["US", "US"],
        })
        self._kline_by_symbol = kline_by_symbol or {}
        self._fail_on = fail_on or set()

    async def get_stock_list(self) -> pd.DataFrame:
        if "stock_list" in self._fail_on:
            raise RuntimeError("simulated akshare failure")
        return self._stock_list.copy()

    async def get_daily_bars(
        self, symbol: str, start_date=None, end_date=None, adjust: str = "qfq",
    ) -> pd.DataFrame:
        if "kline" in self._fail_on or symbol in self._fail_on:
            raise RuntimeError(f"simulated kline failure: {symbol}")
        return self._kline_by_symbol.get(symbol, pd.DataFrame()).copy()


def _us_kline(days: int = 5) -> pd.DataFrame:
    dates = pd.date_range(end="2026-07-07", periods=days, freq="B")
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": [180.0 + i for i in range(days)],
        "收盘": [182.0 + i for i in range(days)],
        "最高": [185.0 + i for i in range(days)],
        "最低": [179.0 + i for i in range(days)],
        "成交量": [50_000_000 + i * 1_000_000 for i in range(days)],
        "成交额": [9_000_000_000.0 + i * 100_000_000 for i in range(days)],
    })


def _mock_session() -> MagicMock:
    s = MagicMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    return s


class TestBuildUSSecurityDf:
    def test_empty_returns_schema(self) -> None:
        df = _build_us_security_df(pd.DataFrame())
        assert df.empty
        assert "symbol" in df.columns

    def test_uppercases_symbols(self) -> None:
        raw = pd.DataFrame({
            "code": ["aapl", "tsla", "MSFT"],
            "name": ["Apple", "Tesla", "Microsoft"],
            "exchange": ["US", "US", "US"],
        })
        df = _build_us_security_df(raw)
        assert len(df) == 3
        assert set(df["symbol"]) == {"AAPL", "TSLA", "MSFT"}
        assert all(df["security_type"] == US_SECURITY_TYPE)
        assert all(df["exchange"] == US_EXCHANGE)
        assert bool(df["is_active"].all())


class TestUSKlineETLSyncSecurities:
    @pytest.mark.asyncio
    async def test_sync_calls_upsert(self) -> None:
        provider = _FakeUSProvider()
        etl = USKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch("data_pipeline.etl.us_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert, \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_security_sync(session, business_date=date(2026, 7, 7))  # type: ignore[arg-type]

        assert result["synced_count"] == 2
        call_args = mock_upsert.call_args
        df_arg = call_args.kwargs["df"]
        assert set(df_arg["symbol"]) == {"AAPL", "TSLA"}
        assert all(df_arg["security_type"] == "us_stock")
        assert call_args.kwargs["index_elements"] == ["symbol"]
        from models.dimensions import DimSecurity
        assert call_args.kwargs["model"] is DimSecurity

    @pytest.mark.asyncio
    async def test_sync_fails_loud(self) -> None:
        provider = _FakeUSProvider(fail_on={"stock_list"})
        etl = USKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch("data_pipeline.etl.us_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.finish_job_failed", new=AsyncMock()) as mock_failed:
            with pytest.raises(RuntimeError, match="simulated akshare failure"):
                await etl.run_security_sync(session, business_date=date(2026, 7, 7))  # type: ignore[arg-type]
        mock_failed.assert_awaited_once()


class TestUSKlineETLRunDailyBars:
    @pytest.mark.asyncio
    async def test_writes_fact_market_bar(self) -> None:
        sid_aapl = uuid4()
        sid_tsla = uuid4()
        provider = _FakeUSProvider(
            kline_by_symbol={"AAPL": _us_kline(5), "TSLA": _us_kline(5)},
        )
        etl = USKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.us_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("AAPL", sid_aapl, "US"),
                ("TSLA", sid_tsla, "US"),
            ]),
        ), patch("data_pipeline.etl.us_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert, \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
                symbols=["AAPL", "TSLA"],
            )

        assert result["synced_count"] == 10
        assert result["failure_count"] == 0
        call_args = mock_upsert.call_args
        df_arg = call_args.kwargs["df"]
        assert set(df_arg["security_id"].unique()) == {sid_aapl, sid_tsla}
        assert all(df_arg["source"] == "akshare.stock_us_hist")
        from models.facts import FactMarketBar
        assert call_args.kwargs["model"] is FactMarketBar

    @pytest.mark.asyncio
    async def test_filters_non_us(self) -> None:
        sid_aapl = uuid4()
        sid_a = uuid4()
        provider = _FakeUSProvider(
            kline_by_symbol={"AAPL": _us_kline(3)},
        )
        etl = USKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.us_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("AAPL", sid_aapl, "US"),
                ("600519", sid_a, "sh"),  # A 股过滤
            ]),
        ), patch("data_pipeline.etl.us_kline_etl.upsert_dataframe", new=AsyncMock()), \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
            )
        assert result["synced_count"] == 3

    @pytest.mark.asyncio
    async def test_continues_on_per_symbol_failure(self) -> None:
        sid_aapl = uuid4()
        sid_tsla = uuid4()
        provider = _FakeUSProvider(
            kline_by_symbol={"AAPL": _us_kline(3)},
            fail_on={"TSLA"},
        )
        etl = USKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.us_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("AAPL", sid_aapl, "US"),
                ("TSLA", sid_tsla, "US"),
            ]),
        ), patch("data_pipeline.etl.us_kline_etl.upsert_dataframe", new=AsyncMock()), \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.us_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
            )
        assert result["synced_count"] == 3
        assert result["failure_count"] == 1
        assert result["failures"][0]["symbol"] == "TSLA"
