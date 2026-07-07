"""test_graph_e2e — LangGraph 6 节点端到端测试（mock LLM）。"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from graph.research_graph import build_research_graph
from graph.state import ResearchWorkflowState


def _initial_state() -> ResearchWorkflowState:
    return {
        "business_date": "2026-07-01",
        "window_type": "close",
        "target_symbol": "",
        "market_snapshot": {"market_regime": "risk_on_expansion"},
        "market_rule_hits": [],
        "sector_snapshots": [],
        "sector_rule_hits_map": {},
        "stock_snapshots": [],
        "stock_rule_hits_map": {},
        "market_outlook": None,
        "sector_outlooks": [],
        "stock_outlooks": [],
        "committee_decision": None,
        "final_report": "",
        "ranked_sectors": [],
        "halt_execution": False,
        "halt_reason": "",
    }


def _patch_llm():
    """通用 LLM mock — 模拟不可用,触发 fallback 路径。"""
    return patch(
        "core.llm.create_llm_client",
        side_effect=RuntimeError("mocked LLM off"),
    )


@pytest.mark.asyncio
async def test_graph_runs_to_completion():
    """跑完整 6 节点流程：market → sector → synth → stock → committee → report。"""
    state = _initial_state()
    state["sector_snapshots"] = [
        {"sector_id": "S1", "sector_name": "电子"},
    ]
    state["stock_snapshots"] = [
        {"symbol": "000636", "code": "000636"},
    ]

    with _patch_llm():
        graph = build_research_graph()
        final = await graph.ainvoke(state)

    assert final["market_outlook"] is not None
    # committee 走 fallback 路径
    if final.get("committee_decision") is not None:
        assert "LLM fallback used" in final["committee_decision"].warnings
    assert final["final_report"] != ""
    assert "A 股投研日报" in final["final_report"]


@pytest.mark.asyncio
async def test_graph_short_circuits_on_panic():
    """panic 市场状态应触发 halt_execution,跳过 sector/stock/committee/renderer。"""
    state = _initial_state()
    state["market_snapshot"] = {"market_regime": "risk_off_panic"}
    state["market_rule_hits"] = [{"rule_level": "block", "message": "panic stop"}]

    with _patch_llm():
        graph = build_research_graph()
        final = await graph.ainvoke(state)

    assert final["halt_execution"] is True
    assert final["sector_outlooks"] == []
    assert final["stock_outlooks"] == []
    assert final["committee_decision"] is None
    assert final["final_report"] == ""


@pytest.mark.asyncio
async def test_graph_skips_stock_when_no_snapshots():
    """无 stock_snapshots 时 sector_synth 直接进 committee。"""
    state = _initial_state()
    state["sector_snapshots"] = [
        {"sector_id": "S1", "sector_name": "电子"},
    ]
    # 不设 stock_snapshots

    with _patch_llm():
        graph = build_research_graph()
        final = await graph.ainvoke(state)

    assert final["stock_outlooks"] == []
    assert final["committee_decision"] is not None
    assert final["final_report"] != ""
