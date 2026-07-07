"""test_futures_kline_etl — P0-T2 期货 K 线 ETL 单测。"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pandas as pd
import pytest

from data_pipeline.etl.futures_kline_etl import (
    FUTURES_EXCHANGE,
    FUTURES_SECURITY_TYPE,
    FuturesKlineETL,
    _build_futures_security_df,
)

logger = logging.getLogger(__name__)


class _FakeFuturesProvider:
    def __init__(
        self,
        *,
        contract_list: pd.DataFrame | None = None,
        kline_by_symbol: dict[str, pd.DataFrame] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self._contract_list = contract_list if contract_list is not None else pd.DataFrame({
            "symbol": ["RB2501", "CU2501", "AU2502"],
            "name": ["螺纹钢2501", "沪铜2501", "沪金2502"],
            "exchange": ["FUT", "FUT", "FUT"],
        })
        self._kline_by_symbol = kline_by_symbol or {}
        self._fail_on = fail_on or set()

    async def get_main_contracts_sina(self) -> pd.DataFrame:
        if "stock_list" in self._fail_on:
            raise RuntimeError("simulated akshare failure")
        return self._contract_list.copy()

    async def get_daily_bars_sina(
        self, symbol: str, start_date=None, end_date=None,
    ) -> pd.DataFrame:
        if "kline" in self._fail_on or symbol in self._fail_on:
            raise RuntimeError(f"simulated kline failure: {symbol}")
        return self._kline_by_symbol.get(symbol, pd.DataFrame()).copy()


def _futures_kline(days: int = 5) -> pd.DataFrame:
    dates = pd.date_range(end="2026-07-07", periods=days, freq="B")
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": [3500.0 + i for i in range(days)],
        "收盘": [3520.0 + i for i in range(days)],
        "最高": [3550.0 + i for i in range(days)],
        "最低": [3490.0 + i for i in range(days)],
        "成交量": [100_000 + i * 10_000 for i in range(days)],
        "成交额": [350_000_000.0 + i * 10_000_000 for i in range(days)],
    })


def _mock_session() -> MagicMock:
    s = MagicMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    return s


class TestBuildFuturesSecurityDf:
    def test_normalizes_futures(self) -> None:
        raw = pd.DataFrame({
            "symbol": ["rb2501", "cu2501"],
            "name": ["螺纹钢", "沪铜"],
            "exchange": ["FUT", "FUT"],
        })
        df = _build_futures_security_df(raw)
        assert len(df) == 2
        assert set(df["symbol"]) == {"RB2501", "CU2501"}
        assert all(df["security_type"] == FUTURES_SECURITY_TYPE)
        assert all(df["exchange"] == FUTURES_EXCHANGE)
        assert bool(df["is_active"].all())


class TestFuturesKlineETLRunDailyBars:
    @pytest.mark.asyncio
    async def test_writes_fact_market_bar_with_none_adj(self) -> None:
        """期货无复权，adj_type 必须固定为 'none'。"""
        sid_rb = uuid4()
        sid_cu = uuid4()
        provider = _FakeFuturesProvider(
            kline_by_symbol={"RB2501": _futures_kline(5), "CU2501": _futures_kline(5)},
        )
        etl = FuturesKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.futures_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("RB2501", sid_rb, "FUT"),
                ("CU2501", sid_cu, "FUT"),
            ]),
        ), patch("data_pipeline.etl.futures_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert, \
             patch("data_pipeline.etl.futures_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.futures_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
            )

        assert result["synced_count"] == 10
        call_args = mock_upsert.call_args
        df_arg = call_args.kwargs["df"]
        # 期货无复权
        assert all(df_arg["adj_type"] == "none")
        assert all(df_arg["source"] == "akshare.futures_main_sina_hist")

    @pytest.mark.asyncio
    async def test_continues_on_per_symbol_failure(self) -> None:
        sid_rb = uuid4()
        sid_cu = uuid4()
        provider = _FakeFuturesProvider(
            kline_by_symbol={"RB2501": _futures_kline(3)},
            fail_on={"CU2501"},
        )
        etl = FuturesKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.futures_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("RB2501", sid_rb, "FUT"),
                ("CU2501", sid_cu, "FUT"),
            ]),
        ), patch("data_pipeline.etl.futures_kline_etl.upsert_dataframe", new=AsyncMock()), \
             patch("data_pipeline.etl.futures_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.futures_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
            )
        assert result["synced_count"] == 3
        assert result["failure_count"] == 1
        assert result["failures"][0]["symbol"] == "CU2501"

    @pytest.mark.asyncio
    async def test_filters_non_futures(self) -> None:
        sid_rb = uuid4()
        sid_a = uuid4()
        provider = _FakeFuturesProvider(
            kline_by_symbol={"RB2501": _futures_kline(2)},
        )
        etl = FuturesKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.futures_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("RB2501", sid_rb, "FUT"),
                ("600519", sid_a, "sh"),
            ]),
        ), patch("data_pipeline.etl.futures_kline_etl.upsert_dataframe", new=AsyncMock()), \
             patch("data_pipeline.etl.futures_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.futures_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_bars(
                session, business_date=date(2026, 7, 7),
            )
        assert result["synced_count"] == 2


class TestFuturesKlineETLSyncSecurities:
    @pytest.mark.asyncio
    async def test_sync_calls_upsert(self) -> None:
        provider = _FakeFuturesProvider()
        etl = FuturesKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch("data_pipeline.etl.futures_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert, \
             patch("data_pipeline.etl.futures_kline_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.futures_kline_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_security_sync(session, business_date=date(2026, 7, 7))  # type: ignore[arg-type]

        assert result["synced_count"] == 3
        call_args = mock_upsert.call_args
        df_arg = call_args.kwargs["df"]
        assert set(df_arg["symbol"]) == {"RB2501", "CU2501", "AU2502"}
        assert all(df_arg["security_type"] == "futures")
