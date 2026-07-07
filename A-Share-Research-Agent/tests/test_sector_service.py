"""P1-T9 板块桑基图服务 — 单元测试。"""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

from services.sector_service import get_sector_flow


def _run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def _build_session(signals_rows, sector_rows):
    """构造 AsyncMock session, 模拟 fetch_signals_latest + sector 映射两次 execute。"""
    # 第一次 execute: fetch_signals_latest 的 SELECT * FROM fact_strategy_output
    # 返回 ORM-like rows 列表
    sigs = MagicMock()
    sigs.scalars.return_value.all.return_value = signals_rows
    # 第二次 execute: sector 映射查询
    sec = MagicMock()
    sec.all.return_value = sector_rows
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[sigs, sec])
    return session


def _strategy_row(entity_id, direction, confidence):
    """模拟 FactStrategyOutput 行的最小属性（fetch_signals_latest 需 attribute access）。"""
    from services.signal_persister import STRATEGY_APPROVED, STRATEGY_BLOCKED

    r = MagicMock()
    r.entity_id = entity_id
    r.entity_type = "stock"
    r.direction = direction
    r.confidence = confidence
    r.invalid_conditions = []
    r.supporting_evidence = []
    r.strategy_type = STRATEGY_APPROVED  # 默认
    return r


def test_get_sector_flow_empty_data_returns_empty() -> None:
    """approved/blocked 都为空 → 空结构 + reason。"""
    session = _build_session([], [])
    data = _run(get_sector_flow(session, business_date=date(2025, 7, 7)))
    assert data["nodes"] == []
    assert data["links"] == []
    assert "当日无" in data["reason"]
    assert data["counts"]["links"] == 0


def test_get_sector_flow_builds_nodes_and_links() -> None:
    """2 个 approved + 1 个 blocked，构造 nodes + links。"""
    rows = [
        _strategy_row("600519", "long", 0.9),
        _strategy_row("000001", "long", 0.7),
        _strategy_row("300750", "short", 0.6),
    ]
    # 第 3 行改为 blocked
    from services.signal_persister import STRATEGY_BLOCKED
    rows[2].strategy_type = STRATEGY_BLOCKED

    sector_rows = [
        ("600519", "sw_801010", "农林牧渔"),
        ("000001", "sw_801010", "农林牧渔"),
        ("300750", "sw_801080", "电子"),
    ]
    session = _build_session(rows, sector_rows)
    data = _run(get_sector_flow(session, business_date=date(2025, 7, 7)))

    assert data["reason"] == "ok"
    assert data["counts"]["sectors"] >= 2
    assert data["counts"]["stocks"] >= 3
    assert data["counts"]["links"] == 3
    for ln in data["links"]:
        assert "source" in ln
        assert "target" in ln
        assert "value" in ln
        assert ln["value"] > 0
        assert ln["kind"] in ("approved", "blocked")


def test_get_sector_flow_truncates_at_max_links() -> None:
    """max_links=2 → 仅保留 2 条 link (按 value 降序)。"""
    rows = [
        _strategy_row(f"60{i:04d}", "long", 0.5 + i * 0.01)
        for i in range(5)
    ]
    sector_rows = [
        (f"60{i:04d}", "sw_801010", "农林牧渔") for i in range(5)
    ]
    session = _build_session(rows, sector_rows)
    data = _run(get_sector_flow(session, business_date=date(2025, 7, 7), max_links=2))
    assert data["counts"]["links"] == 2
    vals = [ln["value"] for ln in data["links"]]
    assert vals == sorted(vals, reverse=True)


def test_get_sector_flow_handles_db_failure() -> None:
    """session.execute 抛错 → 空数据 + reason 描述错误。"""
    session = MagicMock()
    session.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))
    data = _run(get_sector_flow(session, business_date=date(2025, 7, 7)))
    assert data["nodes"] == []
    assert data["reason"].startswith("fetch_signals_latest 失败")


def test_get_sector_flow_unmapped_stock_still_appears() -> None:
    """stock 无 sector 映射 → 仍以 stock 节点出现（orphan 节点）。"""
    rows = [_strategy_row("999999", "long", 0.7)]
    sector_rows = []  # 无映射
    session = _build_session(rows, sector_rows)
    data = _run(get_sector_flow(session, business_date=date(2025, 7, 7)))
    stock_node_ids = [n["id"] for n in data["nodes"] if n["kind"] == "stock"]
    assert "stock_999999" in stock_node_ids
    # orphan 节点无 link
    assert data["counts"]["links"] == 0
