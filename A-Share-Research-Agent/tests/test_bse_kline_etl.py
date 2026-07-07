"""P1-T11 北交所 K 线 ETL — 单元测试。

覆盖：
- _build_bse_security_df: 列表 → dim_security schema
- BSEKlineETL.run_security_sync: 写入 5 只样本, source tag
- BSEKlineETL.run_daily_bars: 写 fact_market_bar
- BSEKlineETL.run_daily_bars: 无 BJ 记录 → 0/0
- BSEKlineETL.run_security_sync: provider 抛错 → 透传
- BSEKlineProvider.normalize_symbol: 6 位 pad 到 8 位
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from data_pipeline.etl import bse_kline_etl as bse_etl_mod
from data_pipeline.etl.bse_kline_etl import (
    BSEKlineETL,
    BSE_EXCHANGE,
    BSE_SECURITY_TYPE,
    BSE_SOURCE_TAG,
    _build_bse_security_df,
)
from data_pipeline.providers.bse_kline_provider import BSEKlineProvider


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


class TestBuildSecurityDf:
    def test_empty_input(self) -> None:
        df = _build_bse_security_df(pd.DataFrame())
        assert df.empty
        assert "symbol" in df.columns
        assert "security_type" in df.columns

    def test_build_from_5_samples(self) -> None:
        src = pd.DataFrame({
            "code": ["830001", "831002", "832003", "833004", "870005"],
            "name": ["北交所股A", "北交所股B", "北交所股C", "北交所股D", "北交所股E"],
            "exchange": ["BJ"] * 5,
        })
        df = _build_bse_security_df(src)
        assert len(df) == 5
        assert (df["security_type"] == BSE_SECURITY_TYPE).all()
        assert (df["exchange"] == "BJ").all()
        assert (df["is_active"] == True).all()  # noqa: E712
        # 6 位 code 应被 pad 到 8 位
        assert df["symbol"].iloc[0] == "00830001"
        assert df["symbol"].iloc[1] == "00831002"


class TestProviderNormalize:
    def test_normalize_6_digits_to_8(self) -> None:
        assert BSEKlineProvider.normalize_symbol("830001") == "00830001"

    def test_normalize_with_prefix(self) -> None:
        assert BSEKlineProvider.normalize_symbol("BJ.830001") == "00830001"
        assert BSEKlineProvider.normalize_symbol("bse.831002") == "00831002"

    def test_normalize_8_digits_unchanged(self) -> None:
        assert BSEKlineProvider.normalize_symbol("83000001") == "83000001"


class TestETLSecuritySync:
    @pytest.mark.asyncio
    async def test_run_security_sync_5_samples(self) -> None:
        """mock provider.get_stock_list 返 5 只样本 + mock DB。"""
        stock_list = pd.DataFrame({
            "code": ["83000001", "83000002", "83000003", "83000004", "83000005"],
            "name": ["股A", "股B", "股C", "股D", "股E"],
            "exchange": ["BJ"] * 5,
        })
        provider = MagicMock()
        provider.get_stock_list = AsyncMock(return_value=stock_list)

        session = MagicMock()
        # OpsAuditService.start_job / finish_job_success 都需要 mock
        from data_pipeline.etl import bse_kline_etl as mod
        mod.OpsAuditService.start_job = AsyncMock(return_value="job-1")
        mod.OpsAuditService.finish_job_success = AsyncMock()
        mod.upsert_dataframe = AsyncMock(return_value=5)

        etl = BSEKlineETL(provider)
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
        from data_pipeline.etl import bse_kline_etl as mod
        mod.OpsAuditService.start_job = AsyncMock(return_value="job-1")
        mod.OpsAuditService.finish_job_failed = AsyncMock()

        etl = BSEKlineETL(provider)
        with pytest.raises(RuntimeError, match="network down"):
            await etl.run_security_sync(
                session, business_date=date(2025, 7, 7),
            )
        mod.OpsAuditService.finish_job_failed.assert_awaited_once()


class TestETLDailyBars:
    @pytest.mark.asyncio
    async def test_run_daily_bars_no_bse_securities(self) -> None:
        """dim_security 中无 BJ 记录 → 0/0 返回。"""
        provider = MagicMock()
        session = MagicMock()
        # load_security_rows 返 1 个 non-BJ 行
        with _stub_bse_module(rows=[("000001", "uuid-1", "SZ")])():
            etl = BSEKlineETL(provider)
            out = await etl.run_daily_bars(
                session, business_date=date(2025, 7, 7), symbols=None,
            )
        assert out["synced_count"] == 0
        assert out["failure_count"] == 0

    @pytest.mark.asyncio
    async def test_run_daily_bars_3_samples_1_failure(self) -> None:
        """3 个 BJ 记录，2 成功 + 1 失败。"""
        provider = MagicMock()
        # 第 1/2 成功, 第 3 抛错
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
        with _stub_bse_module(rows=[
            ("83000001", "uuid-1", "BJ"),
            ("83000002", "uuid-2", "BJ"),
            ("83000003", "uuid-3", "BJ"),
        ])():
            etl = BSEKlineETL(provider)
            out = await etl.run_daily_bars(
                session, business_date=date(2025, 7, 7), symbols=None,
            )
        assert out["synced_count"] >= 2  # 至少 2 行
        assert out["failure_count"] == 1
        assert out["failures"][0]["symbol"] == "83000003"


# ── helper ─────────────────────────


def _stub_bse_module(rows: list | None = None, upsert_mock: AsyncMock | None = None):
    """Stub bse_etl_mod 的 load_security_rows / upsert_dataframe + OpsAudit。

    返回 _Patcher context manager，使用 with 即可。
    """
    from contextlib import contextmanager
    from unittest.mock import patch as _patch

    @contextmanager
    def _ctx():
        # load_security_rows
        with _patch.object(bse_etl_mod, "load_security_rows",
                           new=AsyncMock(return_value=rows if rows is not None else [])):
            # OpsAuditService 静态方法
            with _patch.object(bse_etl_mod.OpsAuditService, "start_job",
                               new=AsyncMock(return_value="job-1")):
                with _patch.object(bse_etl_mod.OpsAuditService, "finish_job_success",
                                   new=AsyncMock()):
                    with _patch.object(bse_etl_mod.OpsAuditService, "finish_job_failed",
                                       new=AsyncMock()):
                        with _patch.object(bse_etl_mod, "upsert_dataframe",
                                           new=upsert_mock or AsyncMock()):
                            yield
    return _ctx
