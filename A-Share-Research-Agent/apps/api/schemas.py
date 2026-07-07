"""apps.api.schemas — 所有 Pydantic Request/Response 模型。"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel


class PipelineRequest(BaseModel):
    business_date: date
    symbols: list[str] | None = None
    event_file_path: str | None = None


class ResearchRequest(BaseModel):
    business_date: date
    window_type: str = "close"


class InlineResearchRequest(BaseModel):
    """支持直接传 market_snapshot / sector_snapshots，免依赖数据库的端到端调试入口。"""

    business_date: date
    window_type: str = "close"
    market_snapshot: dict[str, Any] = {}
    market_rule_hits: list[dict[str, Any]] = []
    sector_snapshots: list[dict[str, Any]] = []
    sector_rule_hits_map: dict[str, list[dict[str, Any]]] = {}
    # 个股层：可选。当 target_symbol + stock_snapshot 同时给出时，会在跑完板块流后追加个股分析。
    target_symbol: str | None = None
    stock_snapshot: dict[str, Any] | None = None
    stock_rule_hits: list[dict[str, Any]] = []


class LiveStockRequest(BaseModel):
    """个股实景分析入口：仅给 symbol，由后端实时拉 a-stock-data 端点构造 stock_snapshot。"""

    symbol: str
    business_date: date | None = None


class ReportRequest(BaseModel):
    """研究报告渲染端点：触发 stock_analyst → committee → report_renderer 三节点。"""

    business_date: date
    window_type: str = "close"
    market_snapshot: dict[str, Any] = {}
    market_rule_hits: list[dict[str, Any]] = []
    sector_snapshots: list[dict[str, Any]] = []
    sector_rule_hits_map: dict[str, list[dict[str, Any]]] = {}
    # 个股层（多条；committee 会按权重排序前 5）
    stock_snapshots: list[dict[str, Any]] = []
    stock_rule_hits_map: dict[str, list[dict[str, Any]]] = {}
