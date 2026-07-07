"""Services 聚合。

`research_context_loader` — 单只股票研究流上下文加载（已有）
`macro_service`          — 4 个宏观端点 + 状态判定（已有）
`signal_persister`       — Committee 决策 → fact_strategy_output 落库（新增）
"""
from .macro_service import (
    MacroSnapshot,
    build_market_regime_from_macro,
    get_macro_snapshot,
)
from .research_context_loader import ResearchContext, ResearchContextLoader
from .signal_persister import (
    STRATEGY_APPROVED,
    STRATEGY_BLOCKED,
    STRATEGY_MARKET,
    STRATEGY_SECTOR,
    SignalPersistResult,
    fetch_signals_latest,
    persist_committee_signals,
)

__all__ = [
    "STRATEGY_APPROVED",
    "STRATEGY_BLOCKED",
    "STRATEGY_MARKET",
    "STRATEGY_SECTOR",
    "MacroSnapshot",
    "ResearchContext",
    "ResearchContextLoader",
    "SignalPersistResult",
    "build_market_regime_from_macro",
    "fetch_signals_latest",
    "get_macro_snapshot",
    "persist_committee_signals",
]
