"""DataRouter 路由注册表 — P3.6 拆分。

原 data_router.py 815 行的部分：
- DataFetchError
- TOOL_CATEGORIES / CORE_METHOD_TABLE / EXTENDED_METHOD_TABLE

本文件只做"声明 + 校验"，不持状态、不调 provider。
"""
from __future__ import annotations

from typing import Any


# ── 异常 ─────────────────────────────────────────
class DataFetchError(RuntimeError):
    """DataRouter 抛出的统一异常（含 provider / method 上下文）。"""


# ── 端点分类（前端 / LLM tool 选择器用）─────────
# 字段 "tools" 保留以兼容老调用方
TOOL_CATEGORIES: dict[str, dict[str, Any]] = {
    "market_breadth": {
        "description": "全市场情绪、涨停生态与资金流",
        "tools": [
            "get_market_fund_flow",
            "get_northbound_flow",
            "get_limit_up_pool",
            "get_limit_down_pool",
            "get_bomb_board_pool",
        ],
    },
    "security_master": {
        "description": "证券主数据与基础资料",
        "tools": [
            "get_stock_list",
            "get_stock_fundamentals",
            "get_index_constituents",
        ],
    },
    "bars_and_indicators": {
        "description": "日线、分钟线等行情数据",
        "tools": [
            "get_daily_bars",
            "get_minute_bars",
            "get_index_daily_bars",
        ],
    },
    "sector_master": {
        "description": "行业/概念板块及成分股",
        "tools": [
            "get_concept_board_list",
            "get_industry_board_list",
            "get_concept_board_components",
            "get_industry_board_components",
            "get_sector_recent_bars",
            "get_sector_fund_flow",
            "get_sector_leaders",
        ],
    },
    "dragon_tiger": {
        "description": "龙虎榜相关（aggregate + list）",
        "tools": ["get_dragon_tiger_list", "get_stock_lhb"],
    },
    "news_and_events": {
        "description": "新闻、研报、监管与宏观资讯",
        "tools": [
            "get_stock_news",
            "get_global_macro_news",
            "get_global_news_aggregated",
            "get_insider_transactions",
            "get_sector_event_signals",
            "get_research_reports",
            "get_regulatory_penalties",
            "get_earnings_forecast",
            "get_analyst_consensus",
            "get_cninfo_announcements",
        ],
    },
    "financial_statements": {
        "description": "个股财务报表",
        "tools": [
            "get_balance_sheet",
            "get_cashflow_statement",
            "get_income_statement",
        ],
    },
    "macro_indicators": {
        "description": "宏观指标（PMI / CPI / M2 / 国债）",
        "tools": [
            "get_macro_pmi",
            "get_macro_cpi",
            "get_macro_m2",
            "get_macro_bond_yield",
        ],
    },
    "hk_southbound": {
        "description": "港股通南向资金（A/H 联动）",
        "tools": ["get_hk_southbound_flow", "get_hk_top_stocks"],
    },
    "research_rag": {
        "description": "卖方研报 RAG（深度推理）",
        "tools": ["get_research_chunks", "search_research"],
    },
    "global_market": {
        "description": "全球行情（港股/美股/指数/大宗/国际）",
        "tools": ["get_global_quote"],
    },
    "bse": {
        "description": "北交所（BSE，含北证 50）",
        "tools": [
            "get_bse_stock_list",
            "get_bse_stock_daily_bars",
            "get_bse_index_daily_bars",
        ],
    },
    "futures": {
        "description": "国内期货主力合约",
        "tools": [
            "get_futures_main_contracts",
            "get_futures_daily_bars",
            "get_futures_sector_spot",
        ],
    },
    "hk_full": {
        "description": "港股全量（非南向）",
        "tools": [
            "get_hk_full_stock_list",
            "get_hk_full_daily_bars",
            "get_hk_index_daily_bars",
        ],
    },
    "us_stock": {
        "description": "美股（基础覆盖）",
        "tools": ["get_us_stock_list"],
    },
}


# ── 核心 method 表（高频，21 个端点）─────────
CORE_METHOD_TABLE: dict[str, dict[str, str]] = {
    "get_stock_list": {"akshare": "get_stock_list"},
    "get_stock_individual_info": {"akshare": "get_stock_individual_info"},
    "get_index_constituents": {"akshare": "get_index_constituents"},
    "get_stock_fundamentals": {"akshare": "get_stock_fundamentals"},
    "get_daily_bars": {"akshare": "get_daily_bars", "sina": "get_daily_bars"},
    "get_market_fund_flow": {"akshare": "get_market_fund_flow"},
    "get_northbound_flow": {"akshare": "get_northbound_flow"},
    "get_individual_fund_flow": {"akshare": "get_individual_fund_flow"},
    "get_limit_up_pool": {"akshare": "get_limit_up_pool"},
    "get_limit_down_pool": {"akshare": "get_limit_down_pool"},
    "get_bomb_board_pool": {"akshare": "get_bomb_board_pool"},
    "get_dragon_tiger_list": {"akshare": "get_dragon_tiger_list"},
    "get_concept_board_list": {"akshare": "get_concept_board_list"},
    "get_industry_board_list": {"akshare": "get_industry_board_list"},
    "get_concept_board_components": {"akshare": "get_concept_board_components"},
    "get_industry_board_components": {"akshare": "get_industry_board_components"},
    "get_sector_recent_bars": {"akshare": "get_sector_recent_bars"},
    "get_sector_fund_flow": {"akshare": "get_sector_fund_flow"},
    "get_sector_leaders": {"akshare": "get_sector_leaders"},
    "get_stock_news": {"akshare": "get_stock_news"},
    "get_global_macro_news": {"akshare": "get_global_macro_news"},
}


# ── 扩展 method 表（按需 / 重资源）─────────
EXTENDED_METHOD_TABLE: dict[str, dict[str, str]] = {
    "get_minute_bars": {"akshare": "get_minute_bars", "sina": "get_minute_bars"},
    "get_stock_lhb": {"akshare": "get_stock_lhb"},
    "get_balance_sheet": {"financial_report": "get_balance_sheet"},
    "get_cashflow_statement": {"financial_report": "get_cashflow_statement"},
    "get_income_statement": {"financial_report": "get_income_statement"},
    "get_global_news_aggregated": {"akshare": "get_global_news_aggregated"},
    "get_sector_event_signals": {"akshare": "get_sector_event_signals"},
    "get_insider_transactions": {"akshare": "get_insider_transactions"},
    "get_earnings_forecast": {"akshare": "get_earnings_forecast"},
    "get_analyst_consensus": {"akshare": "get_analyst_consensus"},
    "get_research_reports": {"akshare": "get_research_reports"},
    "get_regulatory_penalties": {"akshare": "get_regulatory_penalties"},
    "get_macro_pmi": {"akshare": "get_macro_pmi"},
    "get_macro_cpi": {"akshare": "get_macro_cpi"},
    "get_macro_m2": {"akshare": "get_macro_m2"},
    "get_macro_bond_yield": {"akshare": "get_macro_bond_yield"},
    "get_hk_southbound_flow": {"hk_southbound": "get_southbound_flow"},
    "get_hk_top_stocks": {"hk_southbound": "get_top_stocks"},
    "get_research_chunks": {"research_rag": "get_chunks"},
    "search_research": {"research_rag": "search"},
    "get_cninfo_announcements": {"cninfo": "get_cninfo_announcements"},
    "get_global_quote": {"global_quote": "get_global_quote"},
    "get_index_daily_bars": {"sina": "get_index_daily_bars"},
    "get_bse_stock_list": {"bse": "get_stock_list"},
    "get_bse_stock_daily_bars": {"bse": "get_stock_daily_bars"},
    "get_bse_index_daily_bars": {"bse_index": "get_index_daily_bars"},
    "get_futures_main_contracts": {"futures": "get_main_contracts_sina"},
    "get_futures_daily_bars": {"futures": "get_daily_bars_sina"},
    "get_futures_sector_spot": {"futures": "get_sector_spot"},
    "get_hk_full_stock_list": {"hk_full": "get_stock_list"},
    "get_hk_full_daily_bars": {"hk_full": "get_daily_bars"},
    "get_hk_index_daily_bars": {"hk_full": "get_index_daily_bars"},
    "get_us_stock_list": {"us_stock": "get_stock_list"},
}


# ── 合并 + 校验 ─────────────────────────
def merged_method_table() -> dict[str, dict[str, str]]:
    """返回 CORE + EXTENDED 合并的路由表（深拷贝防污染）。"""
    out: dict[str, dict[str, str]] = {}
    for src in (CORE_METHOD_TABLE, EXTENDED_METHOD_TABLE):
        for method, providers in src.items():
            if method in out:
                for prov, fn in providers.items():
                    out[method][prov] = fn
            else:
                out[method] = dict(providers)
    return out


def list_methods() -> list[str]:
    """返回所有已注册 method 名。"""
    return sorted(merged_method_table().keys())


def list_categories() -> list[dict[str, Any]]:
    """返回端点分类的精简列表（前端 / LLM tool 用）。"""
    return [
        {"name": name, "label": meta.get("description") or meta.get("label", name),
         "n_methods": len(meta["tools"])}
        for name, meta in TOOL_CATEGORIES.items()
    ]


def find_category(method: str) -> str | None:
    """反查 method 所属的分类名（前端展示用）。"""
    for cat, meta in TOOL_CATEGORIES.items():
        if method in meta.get("tools", []):
            return cat
    return None
