"""test_hk_kline_etl — P0-T1 港股 K 线 ETL 单测。

覆盖：
- `HKKlineETL.run_security_sync`：HKFullProvider 拉取的港股列表正确构造 dim_security 入库 DataFrame
- `HKKlineETL.run_daily_bars`：用 dim_security.security_id + 港股 K 写 fact_market_bar
- 失败语义：provider 抛错时 ETL 不静默吞错（fail loud）
- 单只股票失败不阻断其他股票

设计：
- 不打外网：用 `_FakeHKProvider` 注入固定数据
- DB 部分用 `unittest.mock` patch `load_security_rows` / `upsert_dataframe`，
  避免依赖 aiosqlite / asyncpg / Docker。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pandas as pd
import pytest

from data_pipeline.etl.hk_kline_etl import (
    HK_EXCHANGE,
    HK_SECURITY_TYPE,
    HKKlineETL,
    _build_hk_security_df,
)

logger = logging.getLogger(__name__)


# ── Fakes ──────────────────────────────────────────────────────────────


class _FakeHKProvider:
    """伪 HKFullProvider：返回固定港股列表 + 固定 K 线。"""

    def __init__(
        self,
        *,
        stock_list: pd.DataFrame | None = None,
        kline_by_symbol: dict[str, pd.DataFrame] | None = None,
        fail_on: set[str] | None = None,
    ) -> None:
        self._stock_list = stock_list if stock_list is not None else pd.DataFrame({
            "code": ["00700", "09988"],
            "name": ["腾讯控股", "阿里巴巴-W"],
            "exchange": ["HK", "HK"],
        })
        self._kline_by_symbol = kline_by_symbol or {}
        self._fail_on = fail_on or set()

    async def get_stock_list(self) -> pd.DataFrame:
        if "stock_list" in self._fail_on:
            raise RuntimeError("simulated akshare failure")
        return self._stock_list.copy()

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if "kline" in self._fail_on or symbol in self._fail_on:
            raise RuntimeError(f"simulated kline failure: {symbol}")
        return self._kline_by_symbol.get(symbol, pd.DataFrame()).copy()


def _hk_kline(days: int = 5) -> pd.DataFrame:
    """构造 N 个交易日的港股 K 线（中文 key，对齐 akshare 真实返回）。"""
    dates = pd.date_range(end="2026-07-07", periods=days, freq="B")
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": [380.0 + i for i in range(days)],
        "收盘": [382.0 + i for i in range(days)],
        "最高": [385.0 + i for i in range(days)],
        "最低": [379.0 + i for i in range(days)],
        "成交量": [10_000_000 + i * 100_000 for i in range(days)],
        "成交额": [3_800_000_000.0 + i * 10_000_000 for i in range(days)],
        "振幅": [1.5] * days,
        "涨跌幅": [0.5, 0.3, -0.2, 0.8, 0.1][:days],
        "涨跌额": [2.0, 1.5, -1.0, 3.0, 0.5][:days],
        "换手率": [0.35] * days,
    })


def _mock_session() -> MagicMock:
    s = MagicMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    return s


# ── 纯函数：_build_hk_security_df ─────────────────────────────────────


class TestBuildHKSecurityDf:
    def test_empty_input_returns_empty_with_schema(self) -> None:
        df = _build_hk_security_df(pd.DataFrame())
        assert df.empty
        assert "symbol" in df.columns
        assert "security_type" in df.columns

    def test_normalizes_hk_security_records(self) -> None:
        raw = pd.DataFrame({
            "code": ["00700", "09988", "03690", "00005"],
            "name": ["腾讯", "阿里", "美团", "汇丰"],
            "exchange": ["HK", "HK", "HK", "HK"],
        })
        df = _build_hk_security_df(raw)
        assert len(df) == 4
        # 全部 zfill(5)
        assert set(df["symbol"]) == {"00700", "09988", "03690", "00005"}
        assert all(df["security_type"] == "hk_stock")
        assert all(df["exchange"] == "HK")
        assert bool(df["is_active"].all())
        # raw_symbol 与 code 一致
        assert set(df["raw_symbol"]) == {"00700", "09988", "03690", "00005"}

    def test_dedupes_when_zfill_collides(self) -> None:
        """zfill 碰撞 → 后续记录覆盖前一条（keep='last'）。"""
        raw = pd.DataFrame({
            "code": ["700", "00700", "00700"],
            "name": ["A", "B", "C"],
            "exchange": ["HK", "HK", "HK"],
        })
        df = _build_hk_security_df(raw)
        # 700 → 00700，00700 → 00700，00700 → 00700，dedupe 后保留最后一条 "C"
        assert len(df) == 1
        assert df.iloc[0]["name"] == "C"

    def test_dedupes_by_symbol(self) -> None:
        raw = pd.DataFrame({
            "code": ["00700", "00700", "00700"],
            "name": ["腾讯", "腾讯", "腾讯控股"],
            "exchange": ["HK", "HK", "HK"],
        })
        df = _build_hk_security_df(raw)
        assert len(df) == 1


# ── run_security_sync ──────────────────────────────────────────────────


class TestHKKlineETLSyncSecurities:
    @pytest.mark.asyncio
    async def test_sync_calls_upsert_with_correct_data(self) -> None:
        provider = _FakeHKProvider()
        etl = HKKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch("data_pipeline.etl.hk_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert:
            with patch(
                "data_pipeline.etl.hk_kline_etl.OpsAuditService.start_job",
                new=AsyncMock(return_value="job-1"),
            ), patch(
                "data_pipeline.etl.hk_kline_etl.OpsAuditService.finish_job_success",
                new=AsyncMock(),
            ):
                result = await etl.run_security_sync(
                    session,  # type: ignore[arg-type]
                    business_date=date(2026, 7, 7),
                )

        assert result["synced_count"] == 2
        assert result["source"] == "akshare.stock_hk_spot_em"
        # 验证 upsert 收到的 df 字段
        call_args = mock_upsert.call_args
        df_arg = call_args.kwargs["df"]
        assert list(df_arg["symbol"]) == ["00700", "09988"]
        assert all(df_arg["security_type"] == HK_SECURITY_TYPE)
        assert all(df_arg["exchange"] == HK_EXCHANGE)
        # index_elements 必须是 symbol（dim_security unique 约束）
        assert call_args.kwargs["index_elements"] == ["symbol"]
        # model 是 DimSecurity
        from models.dimensions import DimSecurity
        assert call_args.kwargs["model"] is DimSecurity

    @pytest.mark.asyncio
    async def test_sync_fails_loud_when_provider_errors(self) -> None:
        provider = _FakeHKProvider(fail_on={"stock_list"})
        etl = HKKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.start_job",
            new=AsyncMock(return_value="job-1"),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.finish_job_failed",
            new=AsyncMock(),
        ) as mock_failed:
            with pytest.raises(RuntimeError, match="simulated akshare failure"):
                await etl.run_security_sync(
                    session,  # type: ignore[arg-type]
                    business_date=date(2026, 7, 7),
                )
        # finish_job_failed 必须被调用（audit 落地）
        mock_failed.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_skips_upsert_on_empty(self) -> None:
        provider = _FakeHKProvider(stock_list=pd.DataFrame())
        etl = HKKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch("data_pipeline.etl.hk_kline_etl.upsert_dataframe", new=AsyncMock()) as mock_upsert:
            with patch(
                "data_pipeline.etl.hk_kline_etl.OpsAuditService.start_job",
                new=AsyncMock(return_value="job-1"),
            ), patch(
                "data_pipeline.etl.hk_kline_etl.OpsAuditService.finish_job_success",
                new=AsyncMock(),
            ):
                result = await etl.run_security_sync(
                    session,  # type: ignore[arg-type]
                    business_date=date(2026, 7, 7),
                )
        assert result["synced_count"] == 0
        mock_upsert.assert_not_called()


# ── run_daily_bars ─────────────────────────────────────────────────────


class TestHKKlineETLRunDailyBars:
    @pytest.mark.asyncio
    async def test_writes_fact_market_bar_with_security_id(self) -> None:
        sid_00700 = uuid4()
        sid_09988 = uuid4()
        provider = _FakeHKProvider(
            kline_by_symbol={
                "00700": _hk_kline(5),
                "09988": _hk_kline(5),
            },
        )
        etl = HKKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.hk_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("00700", sid_00700, "HK"),
                ("09988", sid_09988, "HK"),
            ]),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.upsert_dataframe",
            new=AsyncMock(),
        ) as mock_upsert, patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.start_job",
            new=AsyncMock(return_value="job-1"),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.finish_job_success",
            new=AsyncMock(),
        ):
            result = await etl.run_daily_bars(
                session,  # type: ignore[arg-type]
                business_date=date(2026, 7, 7),
                symbols=["00700", "09988"],
                datalen=30,
            )

        assert result["synced_count"] == 10  # 2 symbols * 5 bars
        assert result["failure_count"] == 0

        # 验证 upsert 收到的 df
        call_args = mock_upsert.call_args
        df_arg = call_args.kwargs["df"]
        assert set(df_arg["security_id"].unique()) == {sid_00700, sid_09988}
        assert all(df_arg["bar_timeframe"] == "1d")
        assert all(df_arg["adj_type"] == "qfq")
        assert all(df_arg["source"] == "akshare.stock_hk_hist")
        # index_elements 必须含 (security_id, bar_date, bar_timeframe, adj_type, version)
        assert set(call_args.kwargs["index_elements"]) == {
            "security_id", "bar_date", "bar_timeframe", "adj_type", "version"
        }
        from models.facts import FactMarketBar
        assert call_args.kwargs["model"] is FactMarketBar

    @pytest.mark.asyncio
    async def test_continues_on_per_symbol_failure(self) -> None:
        sid_00700 = uuid4()
        sid_09988 = uuid4()
        provider = _FakeHKProvider(
            kline_by_symbol={"00700": _hk_kline(5)},
            fail_on={"09988"},
        )
        etl = HKKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.hk_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("00700", sid_00700, "HK"),
                ("09988", sid_09988, "HK"),
            ]),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.upsert_dataframe",
            new=AsyncMock(),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.start_job",
            new=AsyncMock(return_value="job-1"),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.finish_job_success",
            new=AsyncMock(),
        ):
            result = await etl.run_daily_bars(
                session,  # type: ignore[arg-type]
                business_date=date(2026, 7, 7),
                symbols=["00700", "09988"],
            )

        assert result["synced_count"] == 5
        assert result["failure_count"] == 1
        assert result["failures"][0]["symbol"] == "09988"
        assert "simulated kline failure" in result["failures"][0]["error"]

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_hk_securities(self) -> None:
        provider = _FakeHKProvider()
        etl = HKKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.hk_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("600519", uuid4(), "sh"),  # A 股，filter 掉
            ]),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.upsert_dataframe",
            new=AsyncMock(),
        ) as mock_upsert, patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.start_job",
            new=AsyncMock(return_value="job-1"),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.finish_job_success",
            new=AsyncMock(),
        ):
            result = await etl.run_daily_bars(
                session,  # type: ignore[arg-type]
                business_date=date(2026, 7, 7),
                symbols=["600519"],
            )
        assert result["synced_count"] == 0
        assert result["failure_count"] == 0
        mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_non_hk_from_loaded_securities(self) -> None:
        """即使 symbols 里混了 A 股代码，run_daily_bars 仍只处理 HK 记录。"""
        sid_hk = uuid4()
        sid_a = uuid4()
        provider = _FakeHKProvider(
            kline_by_symbol={"00700": _hk_kline(3)},
        )
        etl = HKKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.hk_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[
                ("00700", sid_hk, "HK"),
                ("600519", sid_a, "sh"),  # A 股应被过滤
            ]),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.upsert_dataframe",
            new=AsyncMock(),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.start_job",
            new=AsyncMock(return_value="job-1"),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.finish_job_success",
            new=AsyncMock(),
        ):
            result = await etl.run_daily_bars(
                session,  # type: ignore[arg-type]
                business_date=date(2026, 7, 7),
            )
        # 仅 00700 写入 3 行
        assert result["synced_count"] == 3


# ── run_full ──────────────────────────────────────────────────────────


class TestHKKlineETLFullRun:
    @pytest.mark.asyncio
    async def test_full_pipeline_invokes_both_steps(self) -> None:
        sid_00700 = uuid4()
        provider = _FakeHKProvider(
            kline_by_symbol={"00700": _hk_kline(3)},
        )
        etl = HKKlineETL(provider=provider)  # type: ignore[arg-type]
        session = _mock_session()

        with patch(
            "data_pipeline.etl.hk_kline_etl.upsert_dataframe",
            new=AsyncMock(),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.load_security_rows",
            new=AsyncMock(return_value=[("00700", sid_00700, "HK")]),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.start_job",
            new=AsyncMock(return_value="job-1"),
        ), patch(
            "data_pipeline.etl.hk_kline_etl.OpsAuditService.finish_job_success",
            new=AsyncMock(),
        ):
            result = await etl.run_full(
                session,  # type: ignore[arg-type]
                business_date=date(2026, 7, 7),
                symbols=["00700"],
            )
        assert result["security_sync"]["synced_count"] == 2  # 00700 + 09988
        assert result["kline"]["synced_count"] == 3
