"""test_macro_etl — P0-T5 宏观数据 ETL 单测。"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from data_pipeline.etl.macro_etl import (
    _row_from_snapshot,
    _SNAPSHOT_TO_CODE,
    MacroETL,
    _period_from_asof,
)

logger = logging.getLogger(__name__)


def _mock_session() -> MagicMock:
    s = MagicMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=None)
    return s


class TestPeriodFromAsof:
    def test_extracts_yyyy_mm(self) -> None:
        assert _period_from_asof("2026-07-07") == "2026-07"
        assert _period_from_asof("2026-07-07T08:00:00") == "2026-07"
        assert _period_from_asof("2026-01") == "2026-01"

    def test_falls_back_to_now(self) -> None:
        result = _period_from_asof(None)
        assert len(result) == 7 and result[4] == "-"


class TestRowFromSnapshot:
    def test_returns_none_for_unavailable(self) -> None:
        snap = {"available": False, "value": None}
        row = _row_from_snapshot(
            snap, indicator_code="PMI_MFG", source="akshare", version="v1",
        )
        assert row is None

    def test_returns_none_for_none_value(self) -> None:
        snap = {"available": True, "value": None}
        row = _row_from_snapshot(
            snap, indicator_code="PMI_MFG", source="akshare", version="v1",
        )
        assert row is None

    def test_builds_row_for_available(self) -> None:
        snap = {
            "available": True,
            "value": 50.2,
            "as_of": "2026-07-07",
            "raw": {"pmi": 50.2, "month": "2026-07"},
        }
        row = _row_from_snapshot(
            snap, indicator_code="PMI_MFG", source="akshare.macro_china_pmi", version="v1",
        )
        assert row is not None
        assert row["indicator_code"] == "PMI_MFG"
        assert row["period"] == "2026-07"
        assert row["value"] == 50.2
        assert row["as_of"] == "2026-07-07"
        assert row["payload"] == {"pmi": 50.2, "month": "2026-07"}

    def test_handles_non_numeric_value(self) -> None:
        snap = {"available": True, "value": "N/A", "as_of": "2026-07-07"}
        row = _row_from_snapshot(
            snap, indicator_code="M2_YOY", source="x", version="v1",
        )
        assert row is not None
        assert row["value"] is None


class TestSnapshotToCode:
    def test_covers_four_endpoints(self) -> None:
        assert set(_SNAPSHOT_TO_CODE.keys()) == {"pmi", "cpi", "m2", "bond_yield"}
        assert _SNAPSHOT_TO_CODE["pmi"] == "PMI_MFG"
        assert _SNAPSHOT_TO_CODE["cpi"] == "CPI_YOY"
        assert _SNAPSHOT_TO_CODE["m2"] == "M2_YOY"
        assert _SNAPSHOT_TO_CODE["bond_yield"] == "CN_US_SPREAD"


class TestMacroETLRunDailyIngest:
    @pytest.mark.asyncio
    async def test_ingests_four_endpoints(self) -> None:
        from services.macro_service import MacroSnapshot

        snap = MacroSnapshot(
            pmi={"available": True, "value": 50.2, "as_of": "2026-07-07", "raw": {"pmi": 50.2}},
            cpi={"available": True, "value": 2.1, "as_of": "2026-07-07", "raw": {"cpi": 2.1}},
            m2={"available": True, "value": 9.5, "as_of": "2026-07-07", "raw": {"m2": 9.5}},
            bond_yield={"available": True, "value": 1.5, "as_of": "2026-07-07", "raw": {"spread": 1.5}},
        )
        etl = MacroETL()
        session = _mock_session()

        with patch("data_pipeline.etl.macro_etl.get_macro_snapshot", new=AsyncMock(return_value=snap)), \
             patch("data_pipeline.etl.macro_etl._upsert_indicator", new=AsyncMock(return_value=1)) as mock_upsert, \
             patch("data_pipeline.etl.macro_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.macro_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_ingest(
                session,  # type: ignore[arg-type]
                business_date=date(2026, 7, 7),
            )

        assert result["upserted_count"] == 4
        assert mock_upsert.await_count == 4
        # 4 个端点都成功
        assert all(result["per_indicator"].values())

    @pytest.mark.asyncio
    async def test_continues_when_some_unavailable(self) -> None:
        from services.macro_service import MacroSnapshot

        snap = MacroSnapshot(
            pmi={"available": True, "value": 50.2, "as_of": "2026-07-07", "raw": {"pmi": 50.2}},
            cpi={"available": False, "value": None, "as_of": None, "raw": {}},  # 不可用
            m2={"available": True, "value": 9.5, "as_of": "2026-07-07", "raw": {"m2": 9.5}},
            bond_yield={"available": True, "value": 1.5, "as_of": "2026-07-07", "raw": {"spread": 1.5}},
        )
        etl = MacroETL()
        session = _mock_session()

        with patch("data_pipeline.etl.macro_etl.get_macro_snapshot", new=AsyncMock(return_value=snap)), \
             patch("data_pipeline.etl.macro_etl._upsert_indicator", new=AsyncMock(return_value=1)) as mock_upsert, \
             patch("data_pipeline.etl.macro_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.macro_etl.OpsAuditService.finish_job_success", new=AsyncMock()):
            result = await etl.run_daily_ingest(
                session,  # type: ignore[arg-type]
                business_date=date(2026, 7, 7),
            )

        assert result["upserted_count"] == 3
        assert result["per_indicator"]["CPI_YOY"] is False
        assert result["per_indicator"]["PMI_MFG"] is True

    @pytest.mark.asyncio
    async def test_fails_loud_when_provider_errors(self) -> None:
        etl = MacroETL()
        session = _mock_session()

        with patch(
            "data_pipeline.etl.macro_etl.get_macro_snapshot",
            new=AsyncMock(side_effect=RuntimeError("akshare down")),
        ), patch("data_pipeline.etl.macro_etl.OpsAuditService.start_job", new=AsyncMock(return_value="job-1")), \
             patch("data_pipeline.etl.macro_etl.OpsAuditService.finish_job_failed", new=AsyncMock()) as mock_failed:
            with pytest.raises(RuntimeError, match="akshare down"):
                await etl.run_daily_ingest(
                    session,  # type: ignore[arg-type]
                    business_date=date(2026, 7, 7),
                )
        mock_failed.assert_awaited_once()
