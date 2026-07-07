"""DataRouter — 数据路由层（策略层）。

设计原则：
- **路由是策略**：DataRouter 只负责"用哪个 provider / 怎么 fallback"，不直接调数据。
- **Provider 是实现**：`akshare` / `a_stock_data` / 未来 `baostock` / `tushare` 都是 provider。
- **method_table 是映射**：`{method: {provider_name: callable}}` — 横向扩展新端点只需加一行。
- **零 mock 降级**：失败 = 抛 `DataFetchError`，由调用方决定是否 strict。
- **可观测**：每个 method:provider 都有 `EndpointStats`（成功/失败/延迟/last_error），暴露到 /health。
- **可缓存**：同 method 同参数在 `ROUTE_CACHE_TTL` 秒内复用，避免重复打外网。

端点返回值约定（与 provider 解耦）：
- **DataFrame（pd.DataFrame）— ETL 专用**：批量入库场景，DataFrame 列与上游字段对齐，
  ETL 层 (`data_pipeline/normalizers/`) 负责 normalize。
  例：`get_stock_list` / `get_daily_bars` / `get_market_fund_flow`。
- **list[dict] / dict — 实时/聚合专用**：契约标准化，便于 facade 透传，
  避免 DataFrame 的 `ambiguous truth` 报错。
  例：`get_kline` / `get_tencent_quote` / `get_stock_news`。

兼容层：
- `AShareDataRouter = DataRouter` 旧名等价；
- `AShareDataRouter(akshare_client=...)` 老构造签名仍能跑（内部映射到 providers 字典）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from core.config import settings

# 触发 provider_adapters 模块加载（让 register_all_adapters 在 DataRouter 实例化时调用）
# 注意：register_all_adapters() 不能在模块顶层调用（会循环导入 — DataRouter 类还没定义），
# 实际调用放在 DataRouter.__init__ 内，那时 DataRouter 已就绪。
from data_pipeline import provider_adapters as _provider_adapters
from data_pipeline.cache import CacheBackend, CacheStats, InMemoryCache
from data_pipeline.resilience import ResilienceError, ResiliencePolicy

logger = logging.getLogger(__name__)


def _summarize_request(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """压缩最近一次调用的入参摘要（写入 EndpointStats.request_sample）。"""
    return {
        "args_preview": [str(arg)[:120] for arg in args[:3]],
        "kwargs": {key: str(value)[:120] for key, value in list(kwargs.items())[:8]},
    }


def _summarize_response(result: Any) -> dict[str, Any]:
    """压缩最近一次调用的出参摘要（写入 EndpointStats.response_sample）。"""
    if result is None:
        return {"result_type": "None", "size": 0}
    if isinstance(result, dict):
        return {
            "result_type": "dict",
            "size": len(result),
            "keys": list(result.keys())[:8],
        }
    if isinstance(result, (list, tuple)):
        return {
            "result_type": type(result).__name__,
            "size": len(result),
            "head": result[:2],
        }
    return {
        "result_type": type(result).__name__,
        "repr": str(result)[:160],
    }


# ── 异常类型 ─────────────────────────────────────────────────────────────

class DataFetchError(RuntimeError):
    """所有 provider 失败后抛出。

    - `method`  数据方法名
    - `attempted_providers` 已尝试的 provider 列表
    - `last_error` 最后一个 provider 的原始异常
    """

    def __init__(
        self,
        method: str,
        attempted_providers: list[str],
        last_error: BaseException | None,
    ) -> None:
        self.method = method
        self.attempted_providers = attempted_providers
        self.last_error = last_error
        super().__init__(
            f"数据路由全部失败: method={method} "
            f"attempted={attempted_providers} last_error={last_error}"
        )


# ── 端点健康度统计 ─────────────────────────────────────────────────────

@dataclass
class EndpointStats:
    """单端点 (method:provider) 的健康度统计。

    Attributes:
        method:          对应的 DataRouter method 名
        provider:        provider 实例 key
        total:           累计调用次数（成功 + 失败）
        success:         累计成功次数
        failure:         累计失败次数
        fallback_count:  因"首选 provider 失败"而触发的兜底调用次数
                         （即在 provider chain 中非首位被调用的次数）
        last_error:      最近一次失败的错误信息（截断 200 字符）
        last_called_at:  最近一次调用的时间戳
        latencies_ms:    最近 100 次调用的延迟采样
    """

    method: str
    provider: str
    total: int = 0
    success: int = 0
    failure: int = 0
    fallback_count: int = 0
    last_error: str = ""
    last_status: str = "idle"
    request_sample: dict[str, Any] = field(default_factory=dict)
    response_sample: dict[str, Any] = field(default_factory=dict)
    error_sample: dict[str, Any] = field(default_factory=dict)
    last_called_at: float = 0.0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=100))

    def record(
        self,
        *,
        success: bool,
        latency_ms: float,
        error: str = "",
        is_fallback: bool = False,
        request_sample: dict[str, Any] | None = None,
        response_sample: dict[str, Any] | None = None,
        error_sample: dict[str, Any] | None = None,
    ) -> None:
        self.total += 1
        self.last_called_at = time.time()
        self.latencies_ms.append(latency_ms)
        if request_sample is not None:
            self.request_sample = request_sample
        if success:
            self.success += 1
            self.last_status = "success"
            self.last_error = ""
            if response_sample is not None:
                self.response_sample = response_sample
            self.error_sample = {}
        else:
            self.failure += 1
            self.last_status = "failure"
            self.last_error = error
            self.response_sample = {}
            if error_sample is not None:
                self.error_sample = error_sample
        if is_fallback:
            self.fallback_count += 1

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 0.0

    @property
    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.95)
        idx = min(idx, len(sorted_lat) - 1)
        return sorted_lat[idx]

    @property
    def fallback_rate(self) -> float:
        """fallback 触发率 = fallback_count / total。"""
        return self.fallback_count / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "provider": self.provider,
            "total": self.total,
            "success": self.success,
            "failure": self.failure,
            "success_rate": round(self.success_rate, 3),
            "p95_latency_ms": round(self.p95_latency_ms, 1),
            "fallback_count": self.fallback_count,
            "fallback_rate": round(self.fallback_rate, 3),
            "last_error": self.last_error[:200] if self.last_error else "",
            "last_status": self.last_status,
            "request_sample": self.request_sample,
            "response_sample": self.response_sample,
            "error_sample": self.error_sample,
            "last_called_at": self.last_called_at,
        }


# ── 路由表分类 ────────────────────────────────────────────────────────

class DataRouter:
    """多 provider 数据路由器。

    Args:
        providers:           provider_name → provider_instance
        method_table:        method_name → {provider_name: callable}
        tool_categories:     method_name → 类别名（用于分类）
        cache_ttl:           缓存 TTL（秒）
        default_timeout:     单端点默认超时（秒）
        default_strict:      默认 strict 模式
    """

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
                "get_kline",
                "get_quote_5level",
                "get_tencent_quote",
            ],
        },
        "sector_master": {
            "description": "行业/概念板块及成分股",
            "tools": [
                "get_concept_board_list",
                "get_industry_board_list",
                "get_concept_board_components",
                "get_industry_board_components",
            ],
        },
        "dragon_tiger": {
            "description": "龙虎榜相关（aggregate + list）",
            "tools": ["get_dragon_tiger", "get_dragon_tiger_list", "get_stock_lhb"],
        },
        "news_and_events": {
            "description": "新闻、龙虎榜与宏观资讯",
            "tools": [
                "get_stock_news",
                "get_global_macro_news",
                "get_insider_transactions",
                "get_global_news_aggregated",
                "get_sector_event_signals",
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
        "capital_flow": {
            "description": "个股资金流",
            "tools": ["get_individual_fund_flow"],
        },
        "sector_data": {
            "description": "板块 K 线 / 资金 / 领涨股 / 成分 / 排名",
            "tools": [
                "get_sector_recent_bars",
                "get_sector_fund_flow",
                "get_sector_leaders",
                "get_concept_blocks",
                "get_industry_ranking",
            ],
        },
        "fundamentals": {
            "description": "个股基本面与财务快照",
            "tools": [
                "get_stock_info",
                "get_finance_snapshot",
            ],
        },
        "index_kline": {
            "description": "指数日 K（沪深 300 / 中证 500 等基准对比）",
            "tools": [
                "get_index_daily_bars",
            ],
        },
        # ── P4.3 多市场 ──
        "bse_market": {
            "description": "北京证券交易所（8 开头代码段 + 北证 50 指数）",
            "tools": [
                "get_bse_stock_list",
                "get_bse_stock_daily_bars",
                "get_bse_index_daily_bars",
            ],
        },
        "futures_market": {
            "description": "国内期货主力合约行情（新浪源）",
            "tools": [
                "get_futures_main_contracts",
                "get_futures_daily_bars",
                "get_futures_sector_spot",
            ],
        },
        "hk_market": {
            "description": "港股全量行情（含恒生 / 恒生科技指数）",
            "tools": [
                "get_hk_full_stock_list",
                "get_hk_full_daily_bars",
                "get_hk_index_daily_bars",
            ],
        },
        "us_market": {
            "description": "美股基础行情",
            "tools": [
                "get_us_stock_list",
                "get_us_stock_daily_bars",
                "get_us_spot_quote",
            ],
        },
    }

    # ── method_table 分层 ─────────────────────────────────────────────
    #
    # 拆为 CORE（默认开启）/ EXTENDED（按需启用）：
    # - **CORE_METHOD_TABLE**  实时+ETL 高频端点（默认注册 28 个）
    # - **EXTENDED_METHOD_TABLE**  低频/重资源端点（8 个：财报三表、分钟线、高级事件、个股龙虎榜）
    #
    # 调用方可通过 `DataRouter(..., include_extended=True)` 一次性启用 EXTENDED，
    # 或在运行时通过 `register_method(name, prov_name, attr)` 动态注册。
    #
    # 拆分理由：
    # - 减少默认暴露面，health 端点更聚焦
    # - 财报三表爬得慢、按需按 report 周期
    # - 分钟线按需拉，不进常规 ETL
    # - 高级新闻聚合走 LLM 推理，按需开
    # - 个股龙虎榜只在 commit 阶段按需查

    # ── Adapter 表（优雅处理多 provider 签名不一致） ─────────────
    #
    # key = (method, provider_name)
    # value = adapter(args, kwargs) -> (args, kwargs)
    #
    # 写入 `_METHOD_ADAPTERS` 后，__init__ 会自动用 AdaptedProvider 包装对应方法，
    # route() 调用前自动做参数签名转换。
    #
    # 详见 `data_pipeline/adapters.py`。
    _METHOD_ADAPTERS: dict[tuple[str, str], Any] = {}

    CORE_METHOD_TABLE: dict[str, dict[str, str]] = {
        # 证券主数据
        "get_stock_list": {"akshare": "get_stock_list"},
        "get_stock_fundamentals": {"akshare": "get_stock_fundamentals"},
        "get_index_constituents": {"akshare": "get_index_constituents"},
        # 行情（新浪/网易做 fallback，akshare 反爬时降级）
        "get_daily_bars": {"akshare": "get_daily_bars", "sina": "get_daily_bars"},
        "get_kline": {"a_stock_data": "get_kline", "sina": "get_daily_bars"},
        "get_quote_5level": {"a_stock_data": "get_quote_5level"},
        "get_tencent_quote": {
            "a_stock_data": "get_tencent_quote",
            "sina": "get_tencent_quote",
            "netease": "get_tencent_quote",
        },
        # 资金
        "get_market_fund_flow": {"akshare": "get_market_fund_flow"},
        "get_northbound_flow": {"akshare": "get_northbound_flow"},
        "get_individual_fund_flow": {"akshare": "get_individual_fund_flow"},
        "get_fund_flow_minute": {"a_stock_data": "get_fund_flow_minute"},
        # 涨停生态
        "get_limit_up_pool": {"akshare": "get_limit_up_pool"},
        "get_limit_down_pool": {"akshare": "get_limit_down_pool"},
        "get_bomb_board_pool": {"akshare": "get_bomb_board_pool"},
        # 龙虎榜
        "get_dragon_tiger_list": {"akshare": "get_dragon_tiger_list"},
        "get_dragon_tiger": {"a_stock_data": "get_dragon_tiger"},
        # 板块主数据
        "get_concept_board_list": {"akshare": "get_concept_board_list"},
        "get_industry_board_list": {"akshare": "get_industry_board_list"},
        "get_concept_board_components": {"akshare": "get_concept_board_components"},
        "get_industry_board_components": {"akshare": "get_industry_board_components"},
        "get_concept_blocks": {"a_stock_data": "get_concept_blocks"},
        "get_industry_ranking": {"a_stock_data": "get_industry_ranking"},
        # 新闻
        "get_stock_news": {"akshare": "get_stock_news", "a_stock_data": "get_stock_news"},
        "get_global_macro_news": {"akshare": "get_global_macro_news"},
        "get_cninfo_announcements": {"cninfo": "get_cninfo_announcements"},
        "get_global_quote": {"global_quote": "get_global_quote"},
        # 板块层数据
        "get_sector_recent_bars": {"akshare": "get_sector_recent_bars"},
        "get_sector_fund_flow": {"akshare": "get_sector_fund_flow"},
        "get_sector_leaders": {"akshare": "get_sector_leaders"},
        # 基本面
        "get_stock_info": {"a_stock_data": "get_stock_info", "sina": "get_stock_info"},
        "get_finance_snapshot": {"a_stock_data": "get_finance_snapshot"},
    }

    EXTENDED_METHOD_TABLE: dict[str, dict[str, str]] = {
        # 分钟线（按需拉）— Sina 做 fallback
        "get_minute_bars": {"akshare": "get_minute_bars", "sina": "get_minute_bars"},
        # 个股龙虎榜（commit 阶段按需）
        "get_stock_lhb": {"akshare": "get_stock_lhb"},
        # 财报三表（按 report 周期）— P1：akshare 1.18.64 财务 API 上游损坏，改走东财 RPT_DMSK_FN_*
        "get_balance_sheet": {"financial_report": "get_balance_sheet"},
        "get_cashflow_statement": {"financial_report": "get_cashflow_statement"},
        "get_income_statement": {"financial_report": "get_income_statement"},
        # 高级事件/新闻聚合（LLM 推理按需）
        "get_global_news_aggregated": {"akshare": "get_global_news_aggregated"},
        "get_sector_event_signals": {"akshare": "get_sector_event_signals"},
        "get_insider_transactions": {"akshare": "get_insider_transactions"},
        # ── P0 补强：业绩预告 / 一致预期 / 研报 / 监管处罚 ──
        "get_earnings_forecast": {"akshare": "get_earnings_forecast"},
        "get_analyst_consensus": {"akshare": "get_analyst_consensus"},
        "get_research_reports": {"akshare": "get_research_reports"},
        "get_regulatory_penalties": {"akshare": "get_regulatory_penalties"},
        # ── Macro 补强：PMI / CPI / M2 / 10Y 国债 ──
        "get_macro_pmi": {"akshare": "get_macro_pmi"},
        "get_macro_cpi": {"akshare": "get_macro_cpi"},
        "get_macro_m2": {"akshare": "get_macro_m2"},
        "get_macro_bond_yield": {"akshare": "get_macro_bond_yield"},
        # ── 港股通南向资金（A/H 联动） ──
        "get_hk_southbound_flow": {"hk_southbound": "get_southbound_flow"},
        "get_hk_top_stocks": {"hk_southbound": "get_top_stocks"},
        # ── 卖方研报 RAG（深度推理） ──
        "get_research_chunks": {"research_rag": "get_chunks"},
        "search_research": {"research_rag": "search"},
        # ── 公告层（巨潮 P0） ──
        "get_cninfo_announcements": {"cninfo": "get_cninfo_announcements"},
        # ── 全球行情（国际 P0：港股/美股/指数/大宗） ──
        "get_global_quote": {"global_quote": "get_global_quote"},
        # ── 指数日 K（沪深 300 / 中证 500 等基准；P2.7 回测对比用） ──
        "get_index_daily_bars": {"sina": "get_index_daily_bars"},
        # ── 多市场 P4.3：北交所/期货/港股全量/美股（按需） ──
        # 北交所（BSE）：8 开头代码段，含北证 50 指数
        "get_bse_stock_list": {"bse": "get_stock_list"},
        "get_bse_stock_daily_bars": {"bse": "get_stock_daily_bars"},
        "get_bse_index_daily_bars": {"bse_index": "get_index_daily_bars"},
        # 国内期货主力合约（新浪源，免登录）
        "get_futures_main_contracts": {"futures": "get_main_contracts_sina"},
        "get_futures_daily_bars": {"futures": "get_daily_bars_sina"},
        "get_futures_sector_spot": {"futures": "get_sector_spot"},
        # 港股全量（非南向）
        "get_hk_full_stock_list": {"hk_full": "get_stock_list"},
        "get_hk_full_daily_bars": {"hk_full": "get_daily_bars"},
        "get_hk_index_daily_bars": {"hk_full": "get_index_daily_bars"},
        # 美股（仅基础覆盖）
        "get_us_stock_list": {"us_stock": "get_stock_list"},
        "get_us_stock_daily_bars": {"us_stock": "get_daily_bars"},
        "get_us_spot_quote": {"us_stock": "get_spot_quote"},
    }

    # 兼容旧字段：DEFAULT_METHOD_TABLE 指向 CORE（保持原有调用方零改动）
    DEFAULT_METHOD_TABLE: dict[str, dict[str, str]] = CORE_METHOD_TABLE

    # 单点 method 集合（仅 1 个 provider，无 fallback 链）
    # 这些 method 在主 provider 失败时直接抛 DataFetchError，没有兜底。
    # 暴露到 /health 便于运维识别"单点风险"。
    SINGLE_POINT_METHODS: frozenset[str] = frozenset({
        "get_quote_5level",
        "get_fund_flow_minute",
        "get_dragon_tiger",
        "get_concept_blocks",
        "get_industry_ranking",
        "get_finance_snapshot",
    })

    def __init__(
        self,
        providers: dict[str, Any] | None = None,
        *,
        akshare_client: Any | None = None,  # 兼容老构造签名
        method_table: dict[str, dict[str, str]] | None = None,
        tool_categories: dict[str, dict[str, Any]] | None = None,
        cache_ttl: float | None = None,
        default_timeout: float | None = None,
        default_strict: bool | None = None,
        include_extended: bool = False,
        cache: CacheBackend | None = None,
        default_resilience: ResiliencePolicy | None = None,
    ) -> None:
        # ── 注册 adapter（延迟到 DataRouter 类已完全加载）────────────
        # 必须在 self.providers 注入前完成；否则 __init__ 后面查 _METHOD_ADAPTERS 是空的
        _provider_adapters.register_all_adapters()
        # ── 注入 providers ───────────────────────────────────────────
        # 兼容老调用：先看 akshare_client=，再看 providers=，最后默认（注入 6 个 provider）。
        # 默认 providers 是轻量级 HTTP 客户端，构造时不会触发网络请求，sina / netease 兜底
        # 给 akshare 反爬，hk_southbound / research_rag 只在 include_extended=True 时被引用。
        if providers is None:
            if akshare_client is not None:
                providers = {"akshare": akshare_client}
            else:
                from data_pipeline.providers.a_stock_data_provider import AStockDataProvider
                from data_pipeline.providers.akshare_provider import AkshareProvider
                from data_pipeline.providers.bse_provider import BSEIndexProvider, BSEProvider
                from data_pipeline.providers.cninfo import CninfoProvider
                from data_pipeline.providers.financial_report_provider import FinancialReportProvider
                from data_pipeline.providers.futures_provider import FuturesProvider
                from data_pipeline.providers.global_quote_provider import GlobalQuoteProvider
                from data_pipeline.providers.hk_full_provider import HKFullProvider
                from data_pipeline.providers.hk_southbound_provider import HKSouthboundProvider
                from data_pipeline.providers.netease_provider import NeteaseProvider
                from data_pipeline.providers.research_rag_provider import ResearchRAGProvider
                from data_pipeline.providers.sina_provider import SinaProvider
                from data_pipeline.providers.us_stock_provider import USStockProvider

                providers = {
                    "akshare": AkshareProvider(),
                    "a_stock_data": AStockDataProvider(),
                    "sina": SinaProvider(),
                    "netease": NeteaseProvider(),
                    "hk_southbound": HKSouthboundProvider(),
                    "research_rag": ResearchRAGProvider(),
                    "cninfo": CninfoProvider(),
                    "global_quote": GlobalQuoteProvider(),
                    "financial_report": FinancialReportProvider(),
                    # 多市场（按需启用 — 默认在 EXTENDED 表，include_extended=True 时才注册）
                    "bse": BSEProvider(),
                    "bse_index": BSEIndexProvider(),
                    "futures": FuturesProvider(),
                    "hk_full": HKFullProvider(),
                    "us_stock": USStockProvider(),
                }
        elif "akshare" not in providers and akshare_client is not None:
            providers = {"akshare": akshare_client, **providers}

        self.providers: dict[str, Any] = providers
        # 决定要加载的 method_table：自定义 > CORE(+EXTENDED)
        effective_table: dict[str, dict[str, str]] = (
            method_table if method_table is not None
            else (
                {**self.CORE_METHOD_TABLE, **self.EXTENDED_METHOD_TABLE}
                if include_extended
                else self.CORE_METHOD_TABLE
            )
        )
        self._method_table: dict[str, dict[str, Any]] = {}
        for method, prov_map in effective_table.items():
            self._method_table[method] = {}
            for prov_name, attr_name in prov_map.items():
                if prov_name not in self.providers:
                    continue
                fn = getattr(self.providers[prov_name], attr_name, None)
                if fn is None:
                    logger.warning(
                        "method_table 注册失败: provider=%s method=%s 没有属性 %s",
                        prov_name, method, attr_name,
                    )
                    continue
                # 如果有该 method:provider 的 adapter，包装一下（优雅处理签名不一致）
                adapter = self._METHOD_ADAPTERS.get((method, prov_name))
                if adapter is not None:
                    from data_pipeline.adapters import AdaptedProvider as _AP
                    self._method_table[method][prov_name] = _AP(
                        fn=fn, adapter=adapter, attr_name=attr_name
                    )
                else:
                    self._method_table[method][prov_name] = fn

        self._tool_categories: dict[str, dict[str, Any]] = (
            tool_categories or self.TOOL_CATEGORIES
        )
        # 反向索引：method → category
        self._method_to_category: dict[str, str] = {}
        for cat, info in self._tool_categories.items():
            for tool in info.get("tools", []):
                self._method_to_category[tool] = cat

        # ── 缓存 / 超时 / strict 配置 ────────────────────────────────
        self.cache_ttl: float = (
            cache_ttl if cache_ttl is not None else settings.ROUTE_CACHE_TTL_SEC
        )
        self.default_timeout: float = (
            default_timeout if default_timeout is not None else settings.ROUTE_DEFAULT_TIMEOUT
        )
        self.default_strict: bool = (
            default_strict if default_strict is not None else settings.ROUTE_DEFAULT_STRICT
        )

        # ── TTL 缓存（默认 InMemoryCache，可注入 RedisCache 等自定义实现） ─
        self._cache: CacheBackend = cache if cache is not None else InMemoryCache()
        # ── Cache stampede 保护：per-key asyncio.Lock 串行化并发穿透 ──
        # 避免 N 个协程同时 cache miss 时全部去打外网（雪崩）
        import threading
        self._inflight: dict[str, asyncio.Lock] = {}
        self._inflight_lock = threading.Lock()

        # ── 端点健康度统计 ───────────────────────────────────────────
        self._stats: dict[str, EndpointStats] = {}
        for method, provs in self._method_table.items():
            for prov_name in provs:
                key = f"{method}:{prov_name}"
                self._stats[key] = EndpointStats(method=method, provider=prov_name)

        # ── 韧性策略（按 method:provider 覆盖）──────────────────────────
        self._resilience: dict[str, ResiliencePolicy] = {}
        if default_resilience is not None:
            self._default_resilience: ResiliencePolicy | None = default_resilience
        elif getattr(settings, "ROUTE_DEFAULT_RESILIENCE", False):
            # 生产模式：自动注入轻量级默认韧性（重试 + 限流 + 熔断 + bulkhead）
            from data_pipeline.resilience.base import (
                BulkheadConfig,
                CircuitBreakerConfig,
                RateLimitConfig,
                RetryConfig,
            )
            self._default_resilience = ResiliencePolicy(
                retry=RetryConfig(max_attempts=3, initial_delay_sec=0.5, max_delay_sec=8.0, backoff_factor=2.0),
                rate_limit=RateLimitConfig(rps=5.0, burst=10),
                circuit=CircuitBreakerConfig(failure_threshold=5, recovery_timeout_sec=30.0, window_size=20),
                bulkhead=BulkheadConfig(max_concurrent=10),
            )
            logger.info("DataRouter 自动注入生产默认韧性策略（ROUTE_DEFAULT_RESILIENCE=true）")
        else:
            self._default_resilience = None

    # ── 查询 API ──────────────────────────────────────────────────────

    def available_methods(self) -> list[str]:
        return sorted(self._method_table.keys())

    def register_method(
        self,
        method: str,
        provider: str,
        attr: str | None = None,
        *,
        category: str | None = None,
    ) -> None:
        """运行时动态注册新 method。

        Args:
            method:    method 名（DataRouter.route() 的第一参数）
            provider:  provider 实例 key（必须是 `self.providers` 之一）
            attr:      provider 上要调用的方法名；不传则用 method 同名
            category:  可选分类，登记到 `tool_categories`（便于 /health 分类展示）

        Raises:
            KeyError:  provider 不存在
            AttributeError:  provider 上没有 attr 属性
        """
        if provider not in self.providers:
            raise KeyError(
                f"未知 provider: {provider}（已注册: {sorted(self.providers.keys())}）"
            )
        attr_name = attr or method
        fn = getattr(self.providers[provider], attr_name, None)
        if fn is None:
            raise AttributeError(
                f"provider '{provider}' 上找不到属性 '{attr_name}'"
            )
        # 走 adapter 包装（与 __init__ 中的逻辑保持一致）
        adapter = self._METHOD_ADAPTERS.get((method, provider))
        if adapter is not None:
            from data_pipeline.adapters import AdaptedProvider as _AP
            wrapped: Any = _AP(fn=fn, adapter=adapter, attr_name=attr_name)
        else:
            wrapped = fn
        self._method_table.setdefault(method, {})[provider] = wrapped
        key = f"{method}:{provider}"
        if key not in self._stats:
            self._stats[key] = EndpointStats(method=method, provider=provider)
        if category:
            self._tool_categories.setdefault(category, {"description": category, "tools": []})
            if method not in self._tool_categories[category]["tools"]:
                self._tool_categories[category]["tools"].append(method)
            self._method_to_category[method] = category
        logger.info(
            "动态注册 method: method=%s provider=%s attr=%s category=%s adapter=%s",
            method, provider, attr_name, category or "(none)",
            "yes" if adapter is not None else "no",
        )

    def get_category_for_method(self, method: str) -> str:
        if method in self._method_to_category:
            return self._method_to_category[method]
        raise ValueError(f"未找到 method 对应的类别: {method}")

    def get_provider_chain(self, method: str, provider: str | None = None) -> list[str]:
        if method not in self._method_table:
            raise ValueError(f"不支持的数据方法: {method}")
        providers = self._method_table[method]
        if provider:
            if provider not in providers:
                raise ValueError(f"method {method} 不支持 provider {provider}")
            return [provider]
        return list(providers.keys())

    def health(self) -> dict[str, Any]:
        """返回所有端点健康度，用于 /health 暴露。"""
        endpoints = [s.to_dict() for s in self._stats.values()]
        total = sum(s["total"] for s in endpoints)
        success = sum(s["success"] for s in endpoints)
        # 单点 method 集合（仅 1 个 provider，无 fallback 链 → 风险点）
        single_point_endpoints = [
            {"method": s.method, "provider": s.provider}
            for s in self._stats.values()
            if s.method in self.SINGLE_POINT_METHODS
        ]
        return {
            "router": "DataRouter",
            "providers": list(self.providers.keys()),
            "endpoints_total": len(endpoints),
            "calls_total": total,
            "calls_success": success,
            "calls_success_rate": round(success / total, 3) if total else 0.0,
            "cache": self._cache.stats().to_dict(),
            "single_point_methods": sorted(self.SINGLE_POINT_METHODS),
            "single_point_endpoints": single_point_endpoints,
            "endpoints": endpoints,
        }

    # ── 路由执行（核心） ──────────────────────────────────────────────

    async def route(
        self,
        method: str,
        *args: Any,
        provider: str | None = None,
        strict: bool | None = None,
        timeout: float | None = None,
        use_cache: bool = True,
        **kwargs: Any,
    ) -> Any:
        """按 method 路由到第一个成功的 provider。

        Args:
            method:     方法名（method_table key）
            *args:      透传给 provider 函数的位置参数
            provider:   强制指定 provider（不走 fallback chain）
            strict:     True=任一 provider 失败即抛 DataFetchError；
                        False=全部失败才抛。默认 = self.default_strict
            timeout:    单 provider 调用的 asyncio.wait_for 超时（秒）
            use_cache:  是否使用 TTL 缓存（同 method + 同 args+kwargs 30s 内复用）

        Returns:
            provider 函数的返回值。失败按 strict 决定是否抛错。
        """
        if method not in self._method_table:
            raise ValueError(f"不支持的数据方法: {method}")

        effective_strict = strict if strict is not None else self.default_strict
        effective_timeout = timeout if timeout is not None else self.default_timeout

        # 1) Cache 命中
        if use_cache and self.cache_ttl > 0:
            cache_key = self._build_cache_key(method, args, kwargs)
            cached = self._cache_get(cache_key)
            if cached is not None:
                logger.debug("router 缓存命中: %s", cache_key)
                return cached

        # 1.5) Stampede 保护：cache miss 时拿 per-key 锁，避免 N 协程同时穿透打外网
        if use_cache and self.cache_ttl > 0:
            cache_key = self._build_cache_key(method, args, kwargs)
            with self._inflight_lock:
                inflight_lock = self._inflight.setdefault(cache_key, asyncio.Lock())
            async with inflight_lock:
                # 双重检查：另一个协程可能已填好 cache
                cached = self._cache_get(cache_key)
                if cached is not None:
                    logger.debug("router stampede 二次命中: %s", cache_key)
                    return cached
                # 走 provider chain（在锁内串行化）
                return await self._dispatch_to_chain(
                    method, args, kwargs, provider, effective_strict, effective_timeout,
                    cache_key=cache_key, use_cache=use_cache,
                )

        # 2) 不走 cache：直接 dispatch
        return await self._dispatch_to_chain(
            method, args, kwargs, provider, effective_strict, effective_timeout,
            cache_key=None, use_cache=False,
        )

    # ── Provider chain dispatch ────────────────────────────────────────

    async def _dispatch_to_chain(
        self,
        method: str,
        args: tuple,
        kwargs: dict,
        provider: str | None,
        effective_strict: bool,
        effective_timeout: float,
        *,
        cache_key: str | None,
        use_cache: bool,
    ) -> Any:
        """遍历 provider chain（兜底链），按顺序尝试直到成功。

        - 每个 provider 调用走可选的 `ResiliencePolicy`（重试/限流/熔断/bulkhead）
        - 成功时若 use_cache=True + cache_key 非空 → 写入 TTL 缓存
        - 失败时按 effective_strict 决定是否抛错
        - 全部失败 → raise `DataFetchError`
        """
        chain = self.get_provider_chain(method, provider)
        # P0-T8: 若该 method 处于 failover 状态，重排 chain 优先走 backup
        try:
            from services.provider_failover import apply_failover_to_chain
            chain = apply_failover_to_chain(method, chain)
        except Exception as exc:  # pragma: no cover
            logger.warning("[provider_failover] chain 重排失败: method=%s err=%s", method, exc)
        attempted: list[str] = []
        last_error: BaseException | None = None

        for chain_idx, candidate in enumerate(chain):
            fn = self._method_table[method][candidate]
            stat_key = f"{method}:{candidate}"
            stat = self._stats.get(stat_key, EndpointStats(method=method, provider=candidate))
            is_fallback = chain_idx > 0
            resilience_policy = self.get_resilience(method, candidate)
            request_sample = _summarize_request(args, kwargs)

            attempted.append(candidate)
            t0 = time.monotonic()
            try:
                logger.info(
                    "数据路由执行: method=%s provider=%s fallback=%s resilience=%s args=%s kwargs=%s",
                    method, candidate, is_fallback,
                    "on" if resilience_policy is not None else "off",
                    args, list(kwargs.keys()),
                )
                # 显式绑定循环变量（避免 B023 闭包陷阱 + 更明确的契约）
                def inner_factory(
                    fn=fn, args=args, kwargs=kwargs, timeout=effective_timeout,
                ):
                    return asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
                if resilience_policy is not None:
                    result = await resilience_policy.execute(inner_factory)
                else:
                    result = await inner_factory()
                latency_ms = (time.monotonic() - t0) * 1000.0
                response_sample = _summarize_response(result)
                stat.record(
                    success=True,
                    latency_ms=latency_ms,
                    is_fallback=is_fallback,
                    request_sample=request_sample,
                    response_sample=response_sample,
                )
                self._stats[stat_key] = stat
                if use_cache and cache_key:
                    self._cache_put(cache_key, result)
                return result
            except Exception as exc:
                latency_ms = (time.monotonic() - t0) * 1000.0
                error_sample = {
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:240],
                }
                stat.record(
                    success=False,
                    latency_ms=latency_ms,
                    error=str(exc),
                    is_fallback=is_fallback,
                    request_sample=request_sample,
                    error_sample=error_sample,
                )
                self._stats[stat_key] = stat
                last_error = exc
                strategy_note = f" [{getattr(exc, 'strategy', '')}]" if isinstance(exc, ResilienceError) else ""
                logger.warning(
                    "数据路由失败: method=%s provider=%s fallback=%s%s error=%s",
                    method, candidate, is_fallback, strategy_note, exc,
                )
                if effective_strict:
                    raise DataFetchError(method, attempted, last_error) from exc

        # 全部失败
        raise DataFetchError(method, attempted, last_error) from last_error

    # ── Cache helpers ────────────────────────────────────────────────

    def _build_cache_key(self, method: str, args: tuple, kwargs: dict) -> str:
        """构建稳定 cache key（用 sha256 避免 repr 漂移）。

        历史教训：原实现用 `repr(args) + repr(sorted(kwargs.items()))`，
        不同 Python 版本对同一对象的 repr 会有差异（如 dict 顺序、unicode escape），
        导致跨进程/跨版本 cache 命中错位。
        现在统一走 sha256（method + json 序列化后的 args/kwargs），
        保证 (method, args, kwargs) 严格对应唯一 key。
        """
        import hashlib
        # kwargs 排序确保 dict 顺序无关
        normalized = {
            "method": method,
            "args": list(args),
            "kwargs": sorted(kwargs.items()),
        }
        payload = json.dumps(normalized, default=str, sort_keys=True, ensure_ascii=False)
        return f"{method}|{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"

    def _cache_get(self, key: str) -> Any | None:
        """委托给 CacheBackend；TTL 过期由实现负责。"""
        return self._cache.get(key)

    def _cache_put(self, key: str, value: Any) -> None:
        """委托给 CacheBackend；TTL = self.cache_ttl。"""
        self._cache.set(key, value, ttl=self.cache_ttl)

    # ── 管理 API ─────────────────────────────────────────────────────

    def reset_stats(self) -> None:
        for stat in self._stats.values():
            stat.total = 0
            stat.success = 0
            stat.failure = 0
            stat.fallback_count = 0
            stat.last_error = ""
            stat.last_status = "idle"
            stat.request_sample = {}
            stat.response_sample = {}
            stat.error_sample = {}
            stat.latencies_ms.clear()

    def clear_cache(self) -> None:
        self._cache.clear()

    def cache_stats(self) -> CacheStats:
        """暴露缓存命中率统计。"""
        return self._cache.stats()

    # ── Resilience helpers ──────────────────────────────────────────

    def set_resilience(
        self,
        method: str,
        provider: str,
        policy: ResiliencePolicy | None,
    ) -> None:
        """为 (method, provider) 注入韧性策略。

        Args:
            method:   方法名
            provider: provider key
            policy:   ResiliencePolicy 实例；传 None 移除已有策略
        """
        key = f"{method}:{provider}"
        if policy is None:
            self._resilience.pop(key, None)
        else:
            self._resilience[key] = policy

    def get_resilience(self, method: str, provider: str) -> ResiliencePolicy | None:
        """获取 (method, provider) 的韧性策略（先查覆盖，再查 default）。"""
        key = f"{method}:{provider}"
        if key in self._resilience:
            return self._resilience[key]
        return self._default_resilience

    def resilience_stats(self) -> dict[str, dict[str, dict[str, int]]]:
        """所有 (method, provider) 的韧性统计。"""
        out: dict[str, dict[str, dict[str, int]]] = {}
        for key, policy in self._resilience.items():
            out[key] = policy.stats()
        return out


# ── 兼容 shim ──────────────────────────────────────────────────────────

# 旧类名 = 新类（DataRouter 完整替代 AShareDataRouter）
AShareDataRouter = DataRouter
