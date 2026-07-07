import operator
from typing import Annotated, Any, TypedDict

from schemas.agent_io import AgentOutputEnvelope


class ResearchWorkflowState(TypedDict):
    """LangGraph 全局状态定义"""

    # 基础入参
    business_date: str
    window_type: str
    target_symbol: str  # 可选；个股报告渲染时必填

    # 预计算数据
    market_snapshot: dict[str, Any]
    market_rule_hits: list[dict[str, Any]]

    sector_snapshots: list[dict[str, Any]]
    sector_rule_hits_map: dict[str, list[dict[str, Any]]]  # sector_id -> rule_hits

    # 个股层（新增 — P4 启用）
    stock_snapshots: list[dict[str, Any]]
    stock_rule_hits_map: dict[str, list[dict[str, Any]]]  # symbol -> rule_hits

    # Agent 产出物
    market_outlook: AgentOutputEnvelope | None

    # Reducer 收集所有板块分析结果
    sector_outlooks: Annotated[list[AgentOutputEnvelope], operator.add]

    # Reducer 收集所有个股分析结果
    stock_outlooks: Annotated[list[AgentOutputEnvelope], operator.add]

    # 委员会拍板 + 报告（新增 — P4 启用）
    committee_decision: AgentOutputEnvelope | None
    final_report: str

    # 最终结果
    ranked_sectors: list[dict[str, Any]]

    # 流程控制信号
    halt_execution: bool
    halt_reason: str
