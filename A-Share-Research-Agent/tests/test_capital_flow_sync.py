"""test_capital_flow_sync — P2-T1 资金流 ETL 单元测试。

覆盖：
- CapitalFlowNormalizer.individual_main_force 单股归一
- CapitalFlowNormalizer.market_main_force 大盘归一
- CapitalFlowNormalizer.northbound_summary 北向归一
- CapitalFlowNormalizer.sector_main_force 板块归一
- CapitalFlowSyncJob.run_close_flows: 市场+北向写入
- CapitalFlowSyncJob.run_close_flows: 个股 symbol=[] 时跳过个股
- CapitalFlowSyncJob.run_close_flows: provider 失败 → 标记 failed 但不静默
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pandas as pd
import pytest

from data_pipeline.etl import capital_flow as cf_mod
from data_pipeline.etl.capital_flow import CapitalFlowSyncJob
from data_pipeline.normalizers.capital_flow_normalizer import CapitalFlowNormalizer


class TestCapitalFlowNormalizerIndividual:
    def test_individual_with_main_force_columns(self) -> None:
        raw = pd.DataFrame([{
            "日期": "2025-07-07",
            "主力净流入-净额": 1_234_567.89,
            "超大单净流入-净额": 500_000.00,
            "大单净流入-净额": 200_000.00,
        }])
        sec_id = uuid4()
        df = CapitalFlowNormalizer.individual_main_force(
            raw_df=raw,
            security_id=sec_id,
            business_date=date(2025, 7, 7),
            source="akshare.stock_individual_fund_flow",
        )
        assert len(df) == 1
        assert df["security_id" if "security_id" in df.columns else "entity_id"].iloc[0] == sec_id
        assert df["flow_type"].iloc[0] == "individual_main_force"
        assert float(df["net_inflow"].iloc[0]) == pytest.approx(1_234_567.89, rel=1e-4)

    def test_individual_with_alt_columns(self) -> None:
        """兼容 '今日主力净流入-净额' 等带'今日'前缀的列。"""
        raw = pd.DataFrame([{
            "日期": "2025-07-07",
            "今日主力净流入-净额": -500_000.00,
            "今日超大单净流入-净额": -200_000.00,
            "今日大单净流入-净额": -100_000.00,
        }])
        df = CapitalFlowNormalizer.individual_main_force(
            raw_df=raw,
            security_id=uuid4(),
            business_date=date(2025, 7, 7),
            source="x",
        )
        assert float(df["net_inflow"].iloc[0]) == pytest.approx(-500_000.0, rel=1e-4)

    def test_individual_empty_returns_empty(self) -> None:
        df = CapitalFlowNormalizer.individual_main_force(
            raw_df=pd.DataFrame(),
            security_id=uuid4(),
            business_date=date(2025, 7, 7),
            source="x",
        )
        assert df.empty


class TestCapitalFlowNormalizerMarket:
    def test_market_main_force(self) -> None:
        raw = pd.DataFrame([{
            "日期": "2025-07-07",
            "今日主力净流入-净额": 50_000_000.00,
            "今日超大单净流入-净额": 30_000_000.00,
        }])
        df = CapitalFlowNormalizer.market_main_force(
            raw_df=raw, business_date=date(2025, 7, 7),
            source="akshare.stock_market_fund_flow",
        )
        assert len(df) == 1
        assert df["flow_type"].iloc[0] == "main_force"
        assert float(df["net_inflow"].iloc[0]) == pytest.approx(50_000_000.0, rel=1e-4)

    def test_market_empty_returns_empty(self) -> None:
        df = CapitalFlowNormalizer.market_main_force(
            raw_df=pd.DataFrame(), business_date=date(2025, 7, 7),
            source="x",
        )
        assert df.empty


class TestCapitalFlowNormalizerNorthbound:
    def test_northbound_summary(self) -> None:
        raw = pd.DataFrame([{
            "日期": "2025-07-07",
            "今日净流入": 2_000_000.00,
            "沪股通": 1_000_000.00,
            "深股通": 1_000_000.00,
        }])
        df = CapitalFlowNormalizer.northbound_summary(
            raw_df=raw, business_date=date(2025, 7, 7),
            source="akshare.stock_hsgt_fund_flow_summary_em",
        )
        assert len(df) == 1
        assert df["flow_type"].iloc[0] == "northbound"
        assert float(df["net_inflow"].iloc[0]) == pytest.approx(2_000_000.0, rel=1e-4)


class TestCapitalFlowNormalizerSector:
    def test_sector_main_force(self) -> None:
        sec_id = uuid4()
        df = CapitalFlowNormalizer.sector_main_force(
            sector_id=sec_id,
            business_date=date(2025, 7, 7),
            net_inflow=Decimal("123.45"),
            gross_inflow=Decimal("600.0"),
            gross_outflow=Decimal("500.0"),
            source="aggregated.security_individual_flow",
        )
        assert len(df) == 1
        assert df["flow_type"].iloc[0] == "sector_main_force"
        assert float(df["net_inflow"].iloc[0]) == pytest.approx(123.45, rel=1e-4)


def _stub_cf_module(rows: list | None = None, upsert_mock: AsyncMock | None = None,
                    individual_df: pd.DataFrame | None = None,
                    market_df: pd.DataFrame | None = None,
                    northbound_df: pd.DataFrame | None = None):
    """Stub capital_flow 模块的依赖。

    返回 contextmanager，with 即可。
    """
    from unittest.mock import patch as _patch

    @contextmanager
    def _ctx():
        with _patch.object(cf_mod, "load_security_rows",
                           new=AsyncMock(return_value=rows if rows is not None else [])):
            with _patch.object(cf_mod.OpsAuditService, "start_job",
                               new=AsyncMock(return_value="job-1")):
                with _patch.object(cf_mod.OpsAuditService, "finish_job_success",
                                   new=AsyncMock()):
                    with _patch.object(cf_mod.OpsAuditService, "finish_job_failed",
                                       new=AsyncMock()):
                        with _patch.object(cf_mod, "upsert_dataframe",
                                           new=upsert_mock or AsyncMock()):
                            yield
    return _ctx


class TestCapitalFlowSyncJob:
    @pytest.mark.asyncio
    async def test_run_close_flows_market_and_northbound(self) -> None:
        """无 symbols → 仅写市场+北向，不写个股。"""
        market_df = pd.DataFrame([{
            "日期": "2025-07-07",
            "今日主力净流入-净额": 50_000_000.00,
            "今日超大单净流入-净额": 30_000_000.00,
        }])
        northbound_df = pd.DataFrame([{
            "日期": "2025-07-07",
            "今日净流入": 2_000_000.00,
            "沪股通": 1_000_000.00,
            "深股通": 1_000_000.00,
        }])
        client = MagicMock()
        client.get_market_fund_flow = AsyncMock(return_value=market_df)
        client.get_northbound_fund_flow_summary = AsyncMock(return_value=northbound_df)
        client.get_individual_fund_flow = AsyncMock()

        upsert_mock = AsyncMock()
        with _stub_cf_module(rows=[], upsert_mock=upsert_mock)():
            job = CapitalFlowSyncJob(client)
            with patch.object(cf_mod.CapitalFlowSyncJob, "_aggregate_sector_flows", new=AsyncMock(return_value=pd.DataFrame())):
                out = await job.run_close_flows(MagicMock(), business_date=date(2025, 7, 7))

        assert out["synced_count"] == 2  # market + northbound
        assert out["failure_count"] == 0
        # 未传 symbols → 不应调 get_individual
        client.get_individual_fund_flow.assert_not_awaited()
        upsert_mock.assert_awaited_once()
        call_args = upsert_mock.await_args
        assert call_args.kwargs["model"].__name__ == "FactCapitalFlow"

    @pytest.mark.asyncio
    async def test_run_close_flows_with_individual(self) -> None:
        """传 1 个 symbol → 写市场+北向+个股。"""
        sec_id = uuid4()
        individual_df = pd.DataFrame([{
            "日期": "2025-07-07",
            "主力净流入-净额": 100_000.00,
            "超大单净流入-净额": 50_000.00,
            "大单净流入-净额": 30_000.00,
        }])
        client = MagicMock()
        client.get_market_fund_flow = AsyncMock(return_value=pd.DataFrame())
        client.get_northbound_fund_flow_summary = AsyncMock(return_value=pd.DataFrame())
        client.get_individual_fund_flow = AsyncMock(return_value=individual_df)

        with _stub_cf_module(rows=[("600519", sec_id, "SH")], upsert_mock=AsyncMock())():
            job = CapitalFlowSyncJob(client)
            with patch.object(cf_mod.CapitalFlowSyncJob, "_aggregate_sector_flows", new=AsyncMock(return_value=pd.DataFrame())):
                out = await job.run_close_flows(
                    MagicMock(), business_date=date(2025, 7, 7), symbols=["600519"],
                )

        assert out["synced_count"] == 1  # 仅个股
        assert out["failure_count"] == 0
        client.get_individual_fund_flow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_close_flows_individual_failure_recorded(self) -> None:
        """个股 provider 抛错 → 失败记录，不阻塞主流程。"""
        sec_id = uuid4()
        client = MagicMock()
        client.get_market_fund_flow = AsyncMock(return_value=pd.DataFrame())
        client.get_northbound_fund_flow_summary = AsyncMock(return_value=pd.DataFrame())
        client.get_individual_fund_flow = AsyncMock(side_effect=RuntimeError("net fail"))

        with _stub_cf_module(rows=[("600519", sec_id, "SH")], upsert_mock=AsyncMock())():
            job = CapitalFlowSyncJob(client)
            with patch.object(cf_mod.CapitalFlowSyncJob, "_aggregate_sector_flows", new=AsyncMock(return_value=pd.DataFrame())):
                out = await job.run_close_flows(
                    MagicMock(), business_date=date(2025, 7, 7), symbols=["600519"],
                )

        assert out["failure_count"] == 1
        assert out["failures"][0]["symbol"] == "600519"
        assert "net fail" in out["failures"][0]["error"]
