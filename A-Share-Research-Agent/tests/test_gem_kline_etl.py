"""P1-T12 创业板 K 线 ETL — 单元测试。

覆盖：
- _build_gem_security_df: 列表 → dim_security schema
- GEMKlineETL.run_security_sync: 写入 5 只样本, source tag
- GEMKlineETL.run_daily_bars: 写 fact_market_bar
- GEMKlineETL.run_daily_bars: 无 GEM 记录 → 0/0
- GEMKlineETL.run_security_sync: provider 抛错 → 透传
- GEMKlineProvider.normalize_symbol: 6 位 pad
- GEMKlineProvider.is_gem_symbol: 主板/创业板判定
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data_pipeline.etl import gem_kline_etl as gem_etl_mod
from data_pipeline.etl.gem_kline_etl import (
    GEM_EXCHANGE,
    GEM_SECURITY_TYPE,
    GEMKlineETL,
    _build_gem_security_df,
)
from data_pipeline.providers.gem_kline_provider import GEMKlineProvider


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


class TestBuildSecurityDf:
    def test_empty_input(self) -> None:
        df = _build_gem_security_df(pd.DataFrame())
        assert df.empty
        assert "symbol" in df.columns
        assert "security_type" in df.columns

    def test_build_from_5_samples(self) -> None:
        src = pd.DataFrame({
            "code": ["300001", "300002", "301003", "301004", "300005"],
            "name": ["创板A", "创板B", "创板C", "创板D", "创板E"],
            "exchange": ["SZ"] * 5,
        })
        df = _build_gem_security_df(src)
        assert len(df) == 5
        assert (df["security_type"] == GEM_SECURITY_TYPE).all()
        assert (df["exchange"] == "SZ").all()
        assert (df["is_active"] == True).all()  # noqa: E712
        # 6 位 code 应保留（已 zfill）
        assert df["symbol"].iloc[0] == "300001"
        assert df["symbol"].iloc[2] == "301003"


class TestProviderNormalize:
    def test_normalize_6_digits_unchanged(self) -> None:
        assert GEMKlineProvider.normalize_symbol("300750") == "300750"

    def test_normalize_with_prefix(self) -> None:
        assert GEMKlineProvider.normalize_symbol("SZ.300750") == "300750"
        assert GEMKlineProvider.normalize_symbol("sz.301002") == "301002"

    def test_is_gem_symbol_true(self) -> None:
        assert GEMKlineProvider.is_gem_symbol("300750") is True
        assert GEMKlineProvider.is_gem_symbol("301002") is True
        assert GEMKlineProvider.is_gem_symbol("300001") is True

    def test_is_gem_symbol_false(self) -> None:
        # 主板
        assert GEMKlineProvider.is_gem_symbol("000001") is False
        assert GEMKlineProvider.is_gem_symbol("600519") is False
        # 北交所
        assert GEMKlineProvider.is_gem_symbol("830001") is False
        # 美股
        assert GEMKlineProvider.is_gem_symbol("AAPL") is False
        # 港股
        assert GEMKlineProvider.is_gem_symbol("00700") is False


class TestETLSecuritySync:
    @pytest.mark.asyncio
    async def test_run_security_sync_5_samples(self) -> None:
        """mock provider.get_stock_list 返 5 只样本 + mock DB。"""
        stock_list = pd.DataFrame({
            "code": ["300001", "300002", "301003", "301004", "300005"],
            "name": ["股A", "股B", "股C", "股D", "股E"],
            "exchange": ["SZ"] * 5,
        })
        provider = MagicMock()
        provider.get_stock_list = AsyncMock(return_value=stock_list)

        session = MagicMock()
        from data_pipeline.etl import gem_kline_etl as mod
        mod.OpsAuditService.start_job = AsyncMock(return_value="job-1")
        mod.OpsAuditService.finish_job_success = AsyncMock()
        mod.upsert_dataframe = AsyncMock(return_value=5)

        etl = GEMKlineETL(provider)
        out = await etl.run_security_sync(
            session, business_date=date(2025, 7, 7),
        )
        assert out["synced_count"] == 5
        assert "source" in out
        mod.OpsAuditService.finish_job_success.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_security_sync_provider_error_propagates(self) -> None:
        """provider 抛错 → ETL 透传。"""
        provider = MagicMock()
        provider.get_stock_list = AsyncMock(side_effect=RuntimeError("network down"))

        session = MagicMock()
        from data_pipeline.etl import gem_kline_etl as mod
        mod.OpsAuditService.start_job = AsyncMock(return_value="job-1")
        mod.OpsAuditService.finish_job_failed = AsyncMock()

        etl = GEMKlineETL(provider)
        with pytest.raises(RuntimeError, match="network down"):
            await etl.run_security_sync(
                session, business_date=date(2025, 7, 7),
            )
        mod.OpsAuditService.finish_job_failed.assert_awaited_once()


def _stub_gem_module(rows: list | None = None, upsert_mock: AsyncMock | None = None):
    """Stub gem_etl_mod 的 load_security_rows / upsert_dataframe + OpsAudit。"""
    from contextlib import contextmanager
    from unittest.mock import patch as _patch

    @contextmanager
    def _ctx():
        with _patch.object(gem_etl_mod, "load_security_rows",
                           new=AsyncMock(return_value=rows if rows is not None else [])):
            with _patch.object(gem_etl_mod.OpsAuditService, "start_job",
                               new=AsyncMock(return_value="job-1")):
                with _patch.object(gem_etl_mod.OpsAuditService, "finish_job_success",
                                   new=AsyncMock()):
                    with _patch.object(gem_etl_mod.OpsAuditService, "finish_job_failed",
                                       new=AsyncMock()):
                        with _patch.object(gem_etl_mod, "upsert_dataframe",
                                           new=upsert_mock or AsyncMock()):
                            yield
    return _ctx


class TestETLDailyBars:
    @pytest.mark.asyncio
    async def test_run_daily_bars_no_gem_securities(self) -> None:
        """dim_security 中无 GEM 记录 → 0/0 返回。"""
        provider = MagicMock()
        session = MagicMock()
        # load_security_rows 返 1 个 主板 行
        with _stub_gem_module(rows=[("000001", "uuid-1", "SZ")])():
            etl = GEMKlineETL(provider)
            out = await etl.run_daily_bars(
                session, business_date=date(2025, 7, 7), symbols=None,
            )
        assert out["synced_count"] == 0
        assert out["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_run_daily_bars_filters_mainboard(self) -> None:
        """dim_security 中混合主板+GEM → 仅 GEM 被处理。"""
        provider = MagicMock()
        ok_df = pd.DataFrame({
            "日期": ["2025-07-01"],
            "开盘": [10.0], "收盘": [10.5],
            "最高": [10.6], "最低": [9.9],
            "成交量": [1000], "成交额": [10500],
        })
        provider.get_daily_bars = AsyncMock(return_value=ok_df)

        session = MagicMock()
        with _stub_gem_module(rows=[
            ("300001", "uuid-1", "SZ"),  # GEM
            ("000002", "uuid-2", "SZ"),  # 主板
            ("300003", "uuid-3", "SZ"),  # GEM
        ])():
            etl = GEMKlineETL(provider)
            out = await etl.run_daily_bars(
                session, business_date=date(2025, 7, 7), symbols=None,
            )
        # 应只调 2 次 provider（仅 GEM）
        assert provider.get_daily_bars.await_count == 2
        assert out["synced_count"] == 2
        assert out["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_run_daily_bars_3_samples_1_failure(self) -> None:
        """3 个 GEM 记录，2 成功 + 1 失败。"""
        provider = MagicMock()
        ok_df = pd.DataFrame({
            "日期": ["2025-07-01", "2025-07-02"],
            "开盘": [10.0, 10.5], "收盘": [10.5, 11.0],
            "最高": [10.6, 11.1], "最低": [9.9, 10.4],
            "成交量": [1000, 2000], "成交额": [10500, 22000],
        })
        provider.get_daily_bars = AsyncMock(side_effect=[
            ok_df, ok_df, RuntimeError("third failed"),
        ])

        session = MagicMock()
        with _stub_gem_module(rows=[
            ("300001", "uuid-1", "SZ"),
            ("300002", "uuid-2", "SZ"),
            ("300003", "uuid-3", "SZ"),
        ])():
            etl = GEMKlineETL(provider)
            out = await etl.run_daily_bars(
                session, business_date=date(2025, 7, 7), symbols=None,
            )
        assert out["synced_count"] >= 2
        assert out["failure_count"] == 1
        assert out["failures"][0]["symbol"] == "300003"
