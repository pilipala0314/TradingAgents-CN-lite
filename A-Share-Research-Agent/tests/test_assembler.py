"""test_assembler — 验证字段合成逻辑（无 I/O）。"""
from __future__ import annotations

from datetime import date

import pytest

from data_pipeline.providers.assembler import assemble, pick_sector_stage


class TestAssemble:
    def test_assembles_minimal_snapshot(self):
        snapshot, sector_change_pct = assemble(
            code="000636",
            business_date=date(2026, 7, 1).isoformat(),
            tencent_one={},
            kline=[],
            blocks=[],
            flow=[],
            lhb={},
            info={},
            fin=None,
            news_list=[],
        )
        assert snapshot["symbol"] == "000636"
        assert snapshot["data_freshness"]["business_date"] == "2026-07-01"
        assert "concept_tags" in snapshot
        assert "fundamentals_snapshot" in snapshot
        assert "capital_snapshot" in snapshot
        assert "news_snapshot" in snapshot
        # 战术字段全是顶层
        assert "a_kill" in snapshot
        assert "weak_to_strong" in snapshot

    def test_uses_tencent_pe_ttm(self):
        snapshot, _ = assemble(
            code="000636",
            business_date=date(2026, 7, 1).isoformat(),
            tencent_one={"pe_ttm": 100.0, "pb": 5.0},
            kline=[],
            blocks=[],
            flow=[],
            lhb={},
            info={},
            fin=None,
            news_list=[],
        )
        assert snapshot["pe_ttm"] == 100.0
        assert snapshot["pb"] == 5.0

    def test_fills_concept_tags_from_blocks(self):
        snapshot, sector_change_pct = assemble(
            code="000636",
            business_date=date(2026, 7, 1).isoformat(),
            tencent_one={},
            kline=[],
            blocks=[{"name": "MLCC", "change_pct": 3.5}, {"name": "电子元件", "change_pct": 1.2}],
            flow=[],
            lhb={},
            info={},
            fin=None,
            news_list=[],
        )
        assert "MLCC" in snapshot["concept_tags"]
        assert "电子元件" in snapshot["concept_tags"]
        # sector_change_pct 应该是第一块板块的涨跌幅
        assert sector_change_pct == 3.5

    def test_infers_limit_type_for_st(self):
        snapshot, _ = assemble(
            code="000636",
            business_date=date(2026, 7, 1).isoformat(),
            tencent_one={},
            kline=[],
            blocks=[],
            flow=[],
            lhb={},
            info={"name": "*ST 风华"},
            fin=None,
            news_list=[],
        )
        assert snapshot["is_st"] is True
        assert snapshot["limit_type"] == "st_5pct"

    def test_lhb_famous_buyer_flag(self):
        snapshot, _ = assemble(
            code="000636",
            business_date=date(2026, 7, 1).isoformat(),
            tencent_one={},
            kline=[],
            blocks=[],
            flow=[],
            lhb={
                "records": [{"date": "2026-07-01"}],
                "seats": {
                    "buy": [{"name": "宁波桑田路营业部"}],
                    "sell": [],
                },
            },
            info={},
            fin=None,
            news_list=[],
        )
        assert snapshot["lhb_famous_buy_flag"] is True
        assert snapshot["lhb_listed"] is True


class TestPickSectorStage:
    def test_strengthening_above_5(self):
        assert pick_sector_stage(6.0) == "strengthening"
        assert pick_sector_stage(5.0) == "strengthening"

    def test_confirmed_above_2(self):
        assert pick_sector_stage(3.0) == "confirmed"

    def test_emerging_above_0_5(self):
        assert pick_sector_stage(1.0) == "emerging"

    def test_decay_on_negative(self):
        assert pick_sector_stage(-5.0) == "decaying"

    def test_default_watching(self):
        assert pick_sector_stage(0.0) == "watching"
