import logging
from typing import Literal

from langgraph.graph import END, StateGraph

from agents.committee import CommitteeSynthesizer
from agents.market_agents import MarketRegimeAnalyst
from agents.report_renderer import ReportRenderer
from agents.sector_agents import SectorAnalyst, SectorRankingSynthesizer
from agents.stock_agents import StockAnalyst
from graph.state import ResearchWorkflowState

logger = logging.getLogger(__name__)

# 节点函数
async def node_market_analyst(state: ResearchWorkflowState):
    logger.info("-> Node: MarketAnalyst")
    analyst = MarketRegimeAnalyst()
    envelope = await analyst.run(
        market_snapshot=state["market_snapshot"],
        rule_hits=state["market_rule_hits"]
    )

    # 检查是否触发了硬熔断
    halt = False
    reason = ""
    for hit in state["market_rule_hits"]:
        if hit.get("rule_level") == "block":
            halt = True
            reason = hit.get("message", "Triggered block level market rule")
            break

    # 如果 LLM 判断是极端恐慌，也触发短路
    if envelope.structured_output.get("market_regime") == "risk_off_panic":
        halt = True
        reason = "LLM Evaluated Market as risk_off_panic"

    return {
        "market_outlook": envelope,
        "halt_execution": halt,
        "halt_reason": reason
    }

def route_after_market(state: ResearchWorkflowState) -> Literal["node_sector_analyst", "__end__"]:
    if state.get("halt_execution"):
        logger.warning(f"Market conditions triggered halt: {state.get('halt_reason')}. Short-circuiting workflow.")
        return "__end__"
    return "node_sector_analyst"

async def node_sector_analyst(state: ResearchWorkflowState):
    logger.info("-> Node: SectorAnalyst (Batch)")
    analyst = SectorAnalyst()

    market_regime = state["market_outlook"].structured_output.get("market_regime", "neutral_mixed")

    # 实际项目中可以使用 LangGraph 的 map-reduce 模式并发执行
    # MVP 阶段用 asyncio.gather 手动并发
    import asyncio

    async def _analyze_single_sector(snapshot):
        sector_id = snapshot.get("sector_id", "unknown")
        rule_hits = state.get("sector_rule_hits_map", {}).get(sector_id, [])
        envelope = await analyst.run(snapshot, market_regime, rule_hits)
        # 把 sector_id 补回 structured_output 方便下游使用
        envelope.structured_output["sector_id"] = sector_id
        return envelope

    if not state["sector_snapshots"]:
        logger.warning("未加载到任何 sector_snapshot，跳过板块分析。")
        return {"sector_outlooks": []}

    tasks = [_analyze_single_sector(snapshot) for snapshot in state["sector_snapshots"]]
    envelopes = await asyncio.gather(*tasks)

    return {"sector_outlooks": envelopes}

async def node_sector_synthesizer(state: ResearchWorkflowState):
    logger.info("-> Node: SectorRankingSynthesizer")
    synthesizer = SectorRankingSynthesizer()
    ranked = await synthesizer.run(
        market_regime_envelope=state["market_outlook"],
        sector_envelopes=state["sector_outlooks"]
    )
    return {"ranked_sectors": ranked}


# ── P4 新增节点 ─────────────────────────────────────────────────
async def node_stock_analyst(state: ResearchWorkflowState):
    """个股层分析 — 仅当 state.stock_snapshots 非空时执行。"""
    logger.info("-> Node: StockAnalyst (Batch)")
    analyst = StockAnalyst()
    market_regime = (state["market_outlook"].structured_output or {}).get(
        "market_regime", "neutral_mixed"
    )

    import asyncio

    async def _analyze_single_stock(snapshot: dict) -> object:
        symbol = snapshot.get("symbol") or snapshot.get("code") or "?"
        rule_hits = state.get("stock_rule_hits_map", {}).get(symbol, [])
        # 推断 sector_stage（取 ranked_sectors 中匹配的第一只）
        sector_stage = "watching"
        for s in state.get("ranked_sectors") or []:
            so = (s.get("structured_output") or {}) if isinstance(s, dict) else {}
            if so.get("sector_id") == snapshot.get("sector_id"):
                sector_stage = so.get("sector_stage", "watching")
                break
        envelope = await analyst.run(
            stock_snapshot=snapshot,
            market_regime=market_regime,
            sector_stage=sector_stage,
            rule_hits=rule_hits,
        )
        # 把 symbol 补回 structured_output
        envelope.structured_output["symbol"] = symbol
        return envelope

    stock_snapshots = state.get("stock_snapshots") or []
    if not stock_snapshots:
        logger.warning("未加载到任何 stock_snapshot，跳过个股分析。")
        return {"stock_outlooks": []}
    envelopes = await asyncio.gather(
        *[_analyze_single_stock(s) for s in stock_snapshots]
    )
    return {"stock_outlooks": envelopes}


def route_after_stock_synth(state: ResearchWorkflowState) -> Literal[
    "node_committee", "__end__"
]:
    """sector_synthesizer 之后：若有 stock_snapshots 走 stock_analyst，否则直接进 committee。"""
    if state.get("halt_execution"):
        return "__end__"
    if state.get("stock_snapshots"):
        return "node_stock_analyst"
    return "node_committee"


async def node_committee(state: ResearchWorkflowState):
    logger.info("-> Node: CommitteeSynthesizer")
    committee = CommitteeSynthesizer()
    decision = await committee.run(
        market_outlook=state["market_outlook"],
        sector_outlooks=state.get("sector_outlooks", []),
        stock_outlooks=state.get("stock_outlooks", []),
    )
    return {"committee_decision": decision}


async def node_report_renderer(state: ResearchWorkflowState):
    logger.info("-> Node: ReportRenderer")
    renderer = ReportRenderer()
    md = renderer.render(
        business_date=state["business_date"],
        window_type=state["window_type"],
        market_outlook=state.get("market_outlook"),
        sector_outlooks=state.get("sector_outlooks", []),
        stock_outlooks=state.get("stock_outlooks", []),
        committee_decision=state.get("committee_decision"),
    )
    return {"final_report": md}


# 构建图
def build_research_graph():
    workflow = StateGraph(ResearchWorkflowState)

    workflow.add_node("node_market_analyst", node_market_analyst)
    workflow.add_node("node_sector_analyst", node_sector_analyst)
    workflow.add_node("node_sector_synthesizer", node_sector_synthesizer)
    workflow.add_node("node_stock_analyst", node_stock_analyst)
    workflow.add_node("node_committee", node_committee)
    workflow.add_node("node_report_renderer", node_report_renderer)

    workflow.set_entry_point("node_market_analyst")

    # 条件路由：如果市场极端恶劣，直接结束，不进行板块和个股分析
    workflow.add_conditional_edges(
        "node_market_analyst",
        route_after_market,
        {
            "node_sector_analyst": "node_sector_analyst",
            "__end__": END
        }
    )

    workflow.add_edge("node_sector_analyst", "node_sector_synthesizer")
    # sector_synthesizer 之后：有 stock_snapshots 走个股，否则直接进 committee
    workflow.add_conditional_edges(
        "node_sector_synthesizer",
        route_after_stock_synth,
        {
            "node_stock_analyst": "node_stock_analyst",
            "node_committee": "node_committee",
            "__end__": END,
        },
    )
    workflow.add_edge("node_stock_analyst", "node_committee")
    workflow.add_edge("node_committee", "node_report_renderer")
    workflow.add_edge("node_report_renderer", END)

    return workflow.compile()
