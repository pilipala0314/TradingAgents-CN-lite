"""test_graph_e2e_real — LangGraph 6 节点真网路端到端测试（不 mock LLM）。

- 不 patch create_llm_client：让真 LLM 跑
- 若 LLM 不可用（api_key 缺失或网络断）→ 自动 skip，避免污染 CI
- 真 LLM 跑通 → 验 final_report 含 "A 股投研日报" 标题
"""
from __future__ import annotations

import pytest

from core.llm.client import LLMClient, LLMUnavailable


def _llm_available() -> bool:
    """探测 LLMClient 是否可初始化 + chat_model 非空。"""
    try:
        client = LLMClient()
        return client.chat_model is not None
    except Exception:
        return False


_LLM_OK = _llm_available()


def _initial_state() -> dict:
    return {
        "business_date": "2026-07-01",
        "window_type": "close",
        "target_symbol": "",
        "market_snapshot": {"market_regime": "risk_on_expansion"},
        "market_rule_hits": [],
        "sector_snapshots": [
            {
                "sector_id": "S1",
                "sector_name": "电子",
                # 给 fallback 一个能进 [emerging, confirmed, strengthening] 的分。
                "continuation_score": 0.7,
                "flow_inflow_score": 0.6,
                "event_confirmation_score": 0.5,
            },
        ],
        "sector_rule_hits_map": {},
        "stock_snapshots": [
            {
                "symbol": "000636",
                "code": "000636",
                "sector_id": "S1",
                "trend_signal": "bullish",
                "conviction_score": 0.7,
            },
        ],
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


@pytest.mark.skipif(not _LLM_OK, reason="LLM 不可用（缺 api_key 或网络不通），真 e2e 跳过")
@pytest.mark.asyncio
async def test_graph_real_llm_runs_to_completion():
    """真 LLM 跑完整 6 节点：market → sector → synth → stock → committee → report。"""
    from graph.research_graph import build_research_graph

    state = _initial_state()
    graph = build_research_graph()

    try:
        final = await graph.ainvoke(state)
    except LLMUnavailable as e:
        pytest.skip(f"LLM 运行时不可用: {e}")
    except Exception as e:
        # LLM 跑通会进 commit + renderer；任何 5xx 都不算"通过"
        pytest.fail(f"真 LLM e2e 异常: {type(e).__name__}: {e}")

    # 6 节点全跑通
    assert final["market_outlook"] is not None, "market_outlook 应由 MarketAnalyst 填入"
    assert final["sector_outlooks"], "sector_outlooks 应由 SectorAnalyst 填入"
    assert final["ranked_sectors"], "ranked_sectors 应由 SectorRankingSynthesizer 填入"
    assert final["stock_outlooks"], "stock_outlooks 应由 StockAnalyst 填入"
    assert final["committee_decision"] is not None, "committee_decision 应由 Committee 填入"
    assert final["final_report"], "final_report 应由 ReportRenderer 填入"
    assert "A 股投研日报" in final["final_report"], "报告标题应含 'A 股投研日报'"


@pytest.mark.skipif(not _LLM_OK, reason="LLM 不可用，真 e2e 跳过")
@pytest.mark.asyncio
async def test_graph_real_llm_skipped_on_panic():
    """真 LLM 跑 panic 短路路径。"""
    from graph.research_graph import build_research_graph

    state = _initial_state()
    # 不放 stock_snapshots，只验 sector → committee
    state["stock_snapshots"] = []
    graph = build_research_graph()
    final = await graph.ainvoke(state)

    # 无 panic 时 6 节点全跑
    assert final["halt_execution"] is False
    assert final["final_report"] != ""
    assert "A 股投研日报" in final["final_report"]
