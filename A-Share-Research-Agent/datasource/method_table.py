"""datasource.method_table — 路由表声明（唯一真相源）。

合并原 data_router.py 中 CORE_METHOD_TABLE + EXTENDED_METHOD_TABLE，
消除与 router/registry.py 的重复。

格式：{method_name: {provider_name: provider_attr_name}}
- provider_name 对应 @register_provider(name) 注册的名称
- provider_attr_name 对应 provider 实例上的方法名
"""
from __future__ import annotations

from typing import Any

from datasource.types import MethodTable

# ── 核心路由表（高频端点）─────────────────────────────────────────
CORE_METHOD_TABLE: MethodTable = {
    # 证券主数据
    "get_stock_list": {"akshare": "get_stock_list"},
    "get_stock_individual_info": {"akshare": "get_stock_individual_info"},
    "get_index_constituents": {"akshare": "get_index_constituents"},
    # 行情（多源 fallback：baostock 实测最稳前置，运行期另有自适应排序）
    "get_daily_bars": {"baostock": "get_daily_bars", "akshare": "get_daily_bars", "tushare": "get_daily_bars", "sina": "get_daily_bars"},
    "get_kline": {"a_stock_data": "get_kline", "sina": "get_kline"},
    "get_quote_5level": {"a_stock_data": "get_quote_5level"},
    "get_tencent_quote": {"eastmoney": "get_tencent_quote", "a_stock_data": "get_tencent_quote", "sina": "get_tencent_quote", "netease": "get_tencent_quote"},
    # 资金（多源 fallback）
    "get_market_fund_flow": {"akshare": "get_market_fund_flow", "tushare": "get_market_fund_flow"},
    "get_northbound_flow": {"akshare": "get_northbound_flow", "tushare": "get_northbound_flow"},
    "get_individual_fund_flow": {"eastmoney": "get_individual_fund_flow", "akshare": "get_individual_fund_flow"},
    "get_fund_flow_minute": {"a_stock_data": "get_fund_flow_minute"},
    # 涨停生态
    "get_limit_up_pool": {"akshare": "get_limit_up_pool"},
    "get_limit_down_pool": {"akshare": "get_limit_down_pool"},
    "get_bomb_board_pool": {"akshare": "get_bomb_board_pool"},
    # 龙虎榜
    "get_dragon_tiger_list": {"akshare": "get_dragon_tiger_list"},
    "get_dragon_tiger": {"a_stock_data": "get_dragon_tiger"},
    # 板块
    "get_concept_board_list": {"akshare": "get_concept_board_list"},
    "get_industry_board_list": {"akshare": "get_industry_board_list"},
    "get_concept_board_components": {"akshare": "get_concept_board_components"},
    "get_industry_board_components": {"akshare": "get_industry_board_components"},
    "get_concept_blocks": {"a_stock_data": "get_concept_blocks"},
    "get_industry_ranking": {"eastmoney": "get_industry_ranking", "a_stock_data": "get_industry_ranking", "sina": "get_industry_ranking"},
    "get_sector_recent_bars": {"akshare": "get_sector_recent_bars"},
    "get_sector_fund_flow": {"akshare": "get_sector_fund_flow", "sina": "get_sector_fund_flow"},
    "get_sector_leaders": {"akshare": "get_sector_leaders"},
    # 新闻（多源 fallback）
    "get_stock_news": {"news": "get_stock_news", "akshare": "get_stock_news", "a_stock_data": "get_stock_news"},
    "get_global_macro_news": {"news": "get_global_macro_news", "akshare": "get_global_macro_news"},
    "get_news_flash": {"news": "get_news_flash"},
    "get_cninfo_announcements": {"cninfo": "get_cninfo_announcements"},
    "get_global_quote": {"global_quote": "get_global_quote"},
    # 基本面（多源 fallback）
    "get_stock_info": {"a_stock_data": "get_stock_info", "sina": "get_stock_info"},
    "get_finance_snapshot": {"a_stock_data": "get_finance_snapshot"},
    "get_stock_fundamentals": {"baostock": "get_stock_fundamentals", "akshare": "get_stock_fundamentals", "tushare": "get_stock_fundamentals"},
}

# ── 扩展路由表（低频/按需）─────────────────────────────────────────
EXTENDED_METHOD_TABLE: MethodTable = {
    # 分钟线
    "get_minute_bars": {"akshare": "get_minute_bars", "sina": "get_minute_bars"},
    # 个股龙虎榜
    "get_stock_lhb": {"akshare": "get_stock_lhb"},
    # 财报三表
    "get_balance_sheet": {"financial_report": "get_balance_sheet"},
    "get_cashflow_statement": {"financial_report": "get_cashflow_statement"},
    "get_income_statement": {"financial_report": "get_income_statement"},
    # 高级事件
    "get_global_news_aggregated": {"akshare": "get_global_news_aggregated"},
    "get_sector_event_signals": {"akshare": "get_sector_event_signals"},
    "get_insider_transactions": {"akshare": "get_insider_transactions"},
    "get_earnings_forecast": {"akshare": "get_earnings_forecast"},
    "get_research_reports": {"akshare": "get_research_reports"},
    "get_regulatory_penalties": {"akshare": "get_regulatory_penalties"},
    # 宏观
    "get_macro_pmi": {"akshare": "get_macro_pmi"},
    "get_macro_cpi": {"akshare": "get_macro_cpi"},
    "get_macro_m2": {"akshare": "get_macro_m2"},
    "get_macro_bond_yield": {"akshare": "get_macro_bond_yield"},
    # 港股通南向
    "get_hk_southbound_flow": {"hk_southbound": "get_southbound_flow"},
    "get_hk_top_stocks": {"hk_southbound": "get_top_stocks"},
    # 研报 RAG
    "get_research_chunks": {"research_rag": "get_chunks"},
    "search_research": {"research_rag": "search"},
    # 指数日 K
    "get_index_daily_bars": {"sina": "get_index_daily_bars"},
    # 多市场
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
    "get_us_stock_daily_bars": {"us_stock": "get_daily_bars"},
    "get_us_spot_quote": {"us_stock": "get_spot_quote"},
    # ── 舆情/情绪 ──
    "get_stock_sentiment": {"sentiment": "get_stock_sentiment"},
    "get_hot_stocks": {"sentiment": "get_hot_stocks"},
    "get_sector_sentiment": {"sentiment": "get_sector_sentiment"},
    # ── 政策/监管 ──
    "get_csrc_announcements": {"policy": "get_csrc_announcements"},
    "get_pboc_operations": {"policy": "get_pboc_operations"},
    "get_macro_indicators": {"policy": "get_macro_indicators"},
    "get_policy_news": {"policy": "get_policy_news"},
    # ── 扩展基本面 ──
    "get_earnings_forecast_ext": {"fundamentals_ext": "get_earnings_forecast"},
    "get_institution_holdings": {"fundamentals_ext": "get_institution_holdings"},
    "get_analyst_consensus": {"fundamentals_ext": "get_analyst_consensus", "akshare": "get_analyst_consensus"},
    "get_dividend_history": {"fundamentals_ext": "get_dividend_history"},
    "get_lockup_calendar": {"fundamentals_ext": "get_lockup_calendar"},
    # ── 国际媒体 ──
    "get_wallstreet_news": {"global_media": "get_wallstreet_news"},
    "get_cnbc_news": {"global_media": "get_cnbc_news"},
    "get_yahoo_finance_news": {"global_media": "get_yahoo_finance_news"},
    "get_reuters_news": {"global_media": "get_reuters_news"},
    "get_global_market_news": {"global_media": "get_global_market_news"},
    # ── 地缘政治 ──
    "get_geopolitics_news": {"geopolitics": "get_geopolitics_news"},
    "get_sanctions_news": {"geopolitics": "get_sanctions_news"},
    "get_trade_war_news": {"geopolitics": "get_trade_war_news"},
    "get_geopolitics_risk_score": {"geopolitics": "get_geopolitics_risk_score"},
    # ── 巨潮资讯 ──
    "get_cninfo_news": {"cninfo_news": "get_cninfo_news"},
    "get_cninfo_regulatory": {"cninfo_news": "get_cninfo_regulatory"},
    "get_cninfo_announcements_full": {"cninfo_news": "get_cninfo_announcements_full"},
}

# ── 合并（完整路由表）─────────────────────────────────────────────
FULL_METHOD_TABLE: MethodTable = {**CORE_METHOD_TABLE, **EXTENDED_METHOD_TABLE}

# ── 工具分类（供 API 按类别分组展示）──────────────────────────────
TOOL_CATEGORIES: dict[str, dict[str, Any]] = {
    "market_breadth": {
        "description": "全市场情绪、涨停生态与资金流",
        "tools": ["get_market_fund_flow", "get_northbound_flow", "get_limit_up_pool", "get_limit_down_pool", "get_bomb_board_pool"],
    },
    "security_master": {
        "description": "证券主数据与基础资料",
        "tools": ["get_stock_list", "get_stock_fundamentals", "get_index_constituents"],
    },
    "bars_and_indicators": {
        "description": "日线、分钟线等行情数据",
        "tools": ["get_daily_bars", "get_minute_bars", "get_kline", "get_quote_5level", "get_tencent_quote"],
    },
    "sector_master": {
        "description": "行业/概念板块及成分股",
        "tools": ["get_concept_board_list", "get_industry_board_list", "get_concept_board_components", "get_industry_board_components"],
    },
    "dragon_tiger": {
        "description": "龙虎榜相关（aggregate + list）",
        "tools": ["get_dragon_tiger", "get_dragon_tiger_list", "get_stock_lhb"],
    },
    "news_and_events": {
        "description": "新闻、龙虎榜与宏观资讯",
        "tools": ["get_stock_news", "get_global_macro_news", "get_insider_transactions", "get_global_news_aggregated", "get_sector_event_signals"],
    },
    "financial_statements": {
        "description": "个股财务报表",
        "tools": ["get_balance_sheet", "get_cashflow_statement", "get_income_statement"],
    },
    "capital_flow": {
        "description": "个股资金流",
        "tools": ["get_individual_fund_flow"],
    },
    "sector_data": {
        "description": "板块 K 线 / 资金 / 领涨股 / 成分 / 排名",
        "tools": ["get_sector_recent_bars", "get_sector_fund_flow", "get_sector_leaders", "get_concept_blocks", "get_industry_ranking"],
    },
    "fundamentals": {
        "description": "个股基本面与财务快照",
        "tools": ["get_stock_info", "get_finance_snapshot"],
    },
    "index_kline": {
        "description": "指数日 K（沪深 300 / 中证 500 等基准对比）",
        "tools": ["get_index_daily_bars"],
    },
    "bse_market": {
        "description": "北京证券交易所（8 开头代码段 + 北证 50 指数）",
        "tools": ["get_bse_stock_list", "get_bse_stock_daily_bars", "get_bse_index_daily_bars"],
    },
    "futures_market": {
        "description": "国内期货主力合约行情（新浪源）",
        "tools": ["get_futures_main_contracts", "get_futures_daily_bars", "get_futures_sector_spot"],
    },
    "hk_market": {
        "description": "港股全量行情（含恒生 / 恒生科技指数）",
        "tools": ["get_hk_full_stock_list", "get_hk_full_daily_bars", "get_hk_index_daily_bars"],
    },
    "us_market": {
        "description": "美股基础行情",
        "tools": ["get_us_stock_list", "get_us_stock_daily_bars", "get_us_spot_quote"],
    },
}


def method_to_category() -> dict[str, str]:
    """method → 分类名 反向索引。"""
    return {tool: cat for cat, info in TOOL_CATEGORIES.items() for tool in info["tools"]}


def list_methods() -> list[str]:
    """返回所有已注册 method 名。"""
    return sorted(FULL_METHOD_TABLE.keys())
