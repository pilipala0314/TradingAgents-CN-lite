"""AkShare 数据 Provider（route 模式下的 'akshare' provider 实现）。

本类是 DataSource 的一个 provider 实现。设计目标：

1. **单一职责**：只负责 AkShare 这一个数据源的所有端点。
2. **接口稳定**：方法签名（method name + 参数 + 返回值）= DataSource 的 method_table key。
3. **零 mock 降级**：失败 = 抛异常，由调用方决定是否降级。
4. **异步化**：底层 AkShare 是同步阻塞，统一 `asyncio.to_thread` 包装。
5. **重试机制**：内置指数退避（基于 AKSHARE_RETRY_* 环境变量）。
6. **反爬补丁**：启动时一次性注入 requests 反爬 patch（UA / 节流 / curl_cffi）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

try:
    import akshare as ak
except ModuleNotFoundError:  # pragma: no cover - 依赖缺失时用于给出明确报错
    ak = None

import pandas as pd

from datasource.errors import NonRetryableProviderError, ProviderError
from datasource.providers._shared.utils import standardize_kline_df
from datasource.registry import register_provider

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """安全读取整型环境变量；非法值回退默认，且钳制下限（retry_count<1 会触发 assert 崩溃）。"""
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


# 重试配置（环境变量可覆盖）
# 默认 1 = 单次尝试（内层不重试）：重试统一收敛到 router 的 resilience 层，
# 避免 provider 内层重试 × 外层重试 造成最多 9 次请求放大。
_AKSHARE_RETRY_COUNT = _env_int("AKSHARE_RETRY_COUNT", 1)
_AKSHARE_RETRY_DELAY_SECONDS = _env_float("AKSHARE_RETRY_DELAY_SECONDS", 1.5)

# 东财请求强制绕过代理（默认关闭；企业代理环境下开启会导致东财端点失败）
_EM_BYPASS_PROXY = os.getenv("DATASOURCE_EM_BYPASS_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}


# ── helpers ──────────────────────────────────────────────────────────────

def _extract_market_prefixed_symbol(symbol: str) -> str:
    """Convert bare A-share symbol into the market-prefixed variant AkShare expects."""
    if symbol.startswith(("SH", "SZ", "BJ")):
        return symbol
    if symbol.startswith("6"):
        return f"SH{symbol}"
    if symbol.startswith(("0", "3")):
        return f"SZ{symbol}"
    if symbol.startswith(("4", "8")):
        return f"BJ{symbol}"
    return f"SH{symbol}"


def _init_akshare_anti_scraping_patch() -> None:
    """Apply EastMoney anti-scraping workarounds inspired by TradingAgents-CN-lite."""
    try:
        import requests
    except ModuleNotFoundError:
        return

    if getattr(requests, "_ashare_agent_headers_patched", False):
        return

    try:
        from curl_cffi import requests as curl_requests

        use_curl_cffi = True
    except ModuleNotFoundError:
        curl_requests = None
        use_curl_cffi = False

    original_get = requests.get
    last_request_time = {"time": 0.0}
    rate_limit_lock = threading.Lock()

    def patched_get(url, **kwargs):
        if "eastmoney.com" in url:
            with rate_limit_lock:
                elapsed = time.time() - last_request_time["time"]
                if elapsed < 0.5:
                    time.sleep(0.5 - elapsed)
                last_request_time["time"] = time.time()

        if use_curl_cffi and curl_requests is not None and "eastmoney.com" in url:
            try:
                curl_kwargs = {
                    "timeout": kwargs.get("timeout", 10),
                    "impersonate": "chrome120",
                }
                if "params" in kwargs:
                    curl_kwargs["params"] = kwargs["params"]
                if "data" in kwargs:
                    curl_kwargs["data"] = kwargs["data"]
                if "json" in kwargs:
                    curl_kwargs["json"] = kwargs["json"]
                return curl_requests.get(url, **curl_kwargs)
            except Exception as exc:
                logger.warning("curl_cffi 绕过东财反爬失败，回退 requests: %s", exc)

        if _EM_BYPASS_PROXY and "eastmoney.com" in url:
            kwargs["proxies"] = {"http": None, "https": None}

        headers = kwargs.setdefault("headers", {})
        headers.setdefault(
            "User-Agent",
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8")
        headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        headers.setdefault("Referer", "https://www.eastmoney.com/")
        headers.setdefault("Connection", "keep-alive")
        # 兜底路径必须注入超时：akshare 内部多数 requests.get 不带 timeout，
        # 无超时挂起的请求会让 to_thread worker 线程永久泄漏（协程被 wait_for 取消后线程无法取消）。
        kwargs.setdefault("timeout", 10)

        for attempt in range(3):
            try:
                return original_get(url, **kwargs)
            except Exception as exc:
                error_text = str(exc)
                is_ssl_error = any(token in error_text for token in ("SSL", "ssl", "UNEXPECTED_EOF_WHILE_READING"))
                if is_ssl_error and attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise

    requests.get = patched_get
    requests._ashare_agent_headers_patched = True
    # 注意：不设进程级 socket.setdefaulttimeout，避免污染宿主进程的其他网络调用


# ── Provider 实现 ────────────────────────────────────────────────────────

def _filter_by_symbol(df, symbol: str) -> pd.DataFrame:
    """按 6 位股票代码过滤 akshare 全市场表（兼容中英文列名）。

    akshare 1.18.64 部分接口（业绩预告/分析师榜单/违规处罚）不再接受
    symbol 参数，只能拉全市场后本地过滤。
    """
    symbol = symbol.strip().split(".")[0].zfill(6)
    for col in ("股票代码", "代码", "symbol"):
        if col in df.columns:
            return df[df[col].astype(str).str.zfill(6) == symbol].reset_index(drop=True)
    return df  # 无代码列：返回原表（调用方自行决定）


# 知名游资/机构席位关键词（本地化启发式，替代原 features_engine 外部依赖）。
# 命中任一即标记为"知名席位"，仅用于辅助研判，不构成投资建议。
_FAMOUS_SEAT_KEYWORDS = (
    "拉萨",              # 东方财富拉萨系（团结路/东环路/金珠西路/察古大道）
    "机构专用",
    "深股通专用",
    "沪股通专用",
    "中金财富",
    "华鑫证券上海分公司",
    "国泰君安证券上海江苏路",
    "中信证券上海溧阳路",
    "银河证券绍兴",
    "光大证券宁波解放南路",
    "国信证券深圳泰然九路",
    "申万宏源上海闵行区东川路",
    "财通证券杭州上塘路",
    "东亚前海证券深圳分公司",
)


def _detect_lhb_famous_buyers(reason: str) -> bool:
    """判断龙虎榜上榜原因文本是否命中知名游资/机构席位关键词。"""
    if not reason:
        return False
    return any(kw in reason for kw in _FAMOUS_SEAT_KEYWORDS)


@register_provider("akshare")
class AkshareProvider:
    """AkShare 数据采集 Provider — DataSource 的 'akshare' provider。

    ## 端点契约（Method → Return Type）

    本 provider 暴露 34 个端点，对应 `datasource.method_table` 中所有
    `provider == "akshare"` 的方法。所有端点都是 `async def`，
    内部用 `asyncio.to_thread` 包装同步的 akshare API。

    ### 返回值类型分两类（与下游消费方对齐）

    **DataFrame（pd.DataFrame）— ETL 专用**：
        用于 ETL 任务直接写库的场景（rows-first）。DataFrame 的列名以 akshare 原始
        字段为准（中文为主），ETL 层通过 normalize 步骤转成业务字段。

        - `get_stock_list` / `get_stock_individual_info` / `get_stock_fundamentals`
        - `get_daily_bars` / `get_minute_bars`
        - `get_market_fund_flow` / `get_northbound_flow` / `get_individual_fund_flow`
        - `get_limit_up_pool` / `get_limit_down_pool` / `get_bomb_board_pool`
        - `get_dragon_tiger_list`
        - `get_concept_board_list` / `get_industry_board_list`
        - `get_concept_board_components` / `get_industry_board_components`
        - `get_stock_news` (已统一为 `list[dict]`，见下)
        - `get_sector_recent_bars` / `get_sector_fund_flow` / `get_sector_leaders`
        - `get_global_news_aggregated` (返回 `list[dict]`)
        - `get_balance_sheet` / `get_cashflow_statement` / `get_income_statement`
        - `get_insider_transactions` (DataFrame 占位)

    **list[dict] / dict — Facade/聚合层专用**：
        端点契约标准化，避免 DataFrame 的 `ambiguous truth` 报错，
        方便 `stock_live_context` 等 facade 透传。

        - `get_stock_news(symbol, page_size=20) -> list[dict]`
        - `get_stock_lhb(symbol, days=90) -> pd.DataFrame` （保留 DataFrame 便于 ETL）
        - `get_global_macro_news(business_date, look_back_days) -> list[dict]`
        - `get_global_news_aggregated(...) -> list[dict]`
        - `get_sector_event_signals(sector_name) -> list[dict]`

    ## 失败语义

    - **永不静默降级**：失败直接 raise（外层 `DataSource` 决定是否 fallback）。
    - **重试**：内置指数退避 `AKSHARE_RETRY_COUNT=3, AKSHARE_RETRY_DELAY_SECONDS=1.5`。
    - **反爬补丁**：初始化时 monkey-patch `requests.get`，
      自动加 UA / Referer / 节流 / curl_cffi 绕过东财反爬。

    ## AkShare 字段命名

    保持 akshare 原始中文列名（`代码` / `名称` / `涨跌幅` / `日期` / ...），
    上层消费方（如 ETL）负责转为统一英文字段。
    本类不做列名 normalize，避免双重翻译。
    """

    def __init__(self) -> None:
        _init_akshare_anti_scraping_patch()
        self.retry_count = _AKSHARE_RETRY_COUNT
        self.retry_delay_seconds = _AKSHARE_RETRY_DELAY_SECONDS
        # 启动期接口完整性检查（一次性暴露 akshare 升级 / 版本漂移风险）
        self._validate_akshare_interface_completeness()

    @property
    def name(self) -> str:
        return "akshare"

    async def health_check(self) -> bool:
        """依赖检查：akshare 已安装即可（网络可用性由路由层实测）。"""
        return ak is not None

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "akshare" in provs)

    def _validate_akshare_interface_completeness(self) -> None:
        """启动期检查本 provider 实际调用的所有 `ak.xxx` 是否存在。

        历史教训：akshare 不同版本接口名变化频繁（`stock_zfgg_em` / `stock_zh_a_indicator` 等），
        之前用 `hasattr(ak, X)` fallthrough 返空 DataFrame/[] 会在调用时**无声**失败。
        这里启动一次性扫描，缺关键接口时 logger.warning — 至少留痕，
        让运维/监控能在第一时间发现 akshare 升级带来的回归。
        """
        if ak is None:
            return  # _ensure_akshare() 会给出明确报错
        required_interfaces = {
            # 主数据
            "stock_zh_a_spot_em": "get_stock_list",
            "stock_individual_info_em": "get_stock_individual_info",
            "stock_financial_analysis_indicator_em": "get_stock_fundamentals（东财财务分析指标）",
            # stock_zh_a_indicator / stock_zh_finance_indicator 已移除，代码已优雅降级
            # 行情
            "stock_zh_a_hist": "get_daily_bars",
            "stock_zh_a_hist_min_em": "get_minute_bars",
            # 资金流
            "stock_market_fund_flow": "get_market_fund_flow",
            "stock_hsgt_fund_flow_summary_em": "get_northbound_flow",
            "stock_individual_fund_flow": "get_individual_fund_flow",
            # 涨停生态
            "stock_zt_pool_em": "get_limit_up_pool",
            "stock_zt_pool_dtgc_em": "get_limit_down_pool",
            "stock_zt_pool_zbgc_em": "get_bomb_board_pool",
            # 龙虎榜
            "stock_lhb_detail_em": "get_dragon_tiger_list",
            "stock_lhb_stock_detail_em": "get_stock_lhb",
            # 板块
            "stock_board_concept_name_em": "get_concept_board_list",
            "stock_board_industry_name_em": "get_industry_board_list",
            "stock_board_concept_cons_em": "get_concept_board_components",
            "stock_board_industry_cons_em": "get_industry_board_components",
            "stock_board_industry_hist_em": "get_sector_recent_bars",
            "stock_sector_fund_flow_hist": "get_sector_fund_flow",
            # stock_board_industry_news_em 已移除，get_sector_event_signals 优雅降级返 []
            # 新闻
            "stock_news_em": "get_stock_news",
            "news_cctv": "get_global_macro_news",
            # P0 补强
            "stock_yjyg_em": "get_earnings_forecast",
            "stock_analyst_rank_em": "get_analyst_consensus",
            "stock_research_report_em": "get_research_reports",
            # stock_zfgg_em 已移除，get_regulatory_penalties 已做优雅降级返 []
            "stock_xgsglb_em": "get_regulatory_penalties（兆底）",
            # 宏观
            "macro_china_pmi_yearly": "get_macro_pmi",
            "macro_china_cpi_yearly": "get_macro_cpi",
            "macro_china_m2_yearly": "get_macro_m2",
            "bond_zh_us_rate": "get_macro_bond_yield",
        }
        missing = {
            name: used_by
            for name, used_by in required_interfaces.items()
            if not hasattr(ak, name)
        }
        if missing:
            missing_lines = "\n  ".join(
                f"ak.{name}  ← 用途: {used_by}" for name, used_by in missing.items()
            )
            logger.warning(
                "AkShare 接口完整性检查：缺失 %d 个接口（akshare 版本可能不兼容）：\n  %s\n"
                "这些端点运行时将返回空 DataFrame/[]，请检查 akshare 版本。",
                len(missing),
                missing_lines,
            )
        else:
            logger.debug("AkShare 接口完整性检查通过（共 %d 个接口）", len(required_interfaces))

    @staticmethod
    def _ensure_akshare():
        if ak is None:
            raise ModuleNotFoundError(
                "未检测到 akshare 依赖，请先安装项目依赖后再执行数据采集任务。"
            )
        return ak

    @staticmethod
    def _is_non_retryable_error(exc: Exception) -> bool:
        """检测是否为不可重试错误（参数校验/数据不存在等逻辑错误）。"""
        msg = str(exc)
        non_retryable_keywords = [
            "只能获取",   # e.g. "跌停股池只能获取最近30个交易日的数据"
            "不存在",     # 数据不存在
            "无效",       # 参数无效
            "参数错误",   # 参数错误
            "IndexError", # 索引越界
        ]
        return any(kw in msg for kw in non_retryable_keywords)

    @staticmethod
    def _is_akshare_empty_data_bug(exc: BaseException) -> bool:
        """识别 akshare 1.18.64 空数据 bug（无数据时内部 None 下标崩溃）。

        这是「确实无数据」的合法业务信号，必须原样抛 TypeError，
        让调用方的 `except TypeError` 分支返回空表——
        若被 _run_with_retry 包装成 ProviderError，该兜底分支就失效了。
        """
        return isinstance(exc, TypeError) and "not subscriptable" in str(exc)

    async def _run_with_retry(
        self,
        func: Callable[..., pd.DataFrame],
        *args: Any,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """重试：指数退避（attempt^2 倍数）+ 不可重试错误快速失败。

        退避时间序列：1×base, 4×base, 9×base, ...（默认 base=1s → 1s, 4s, 9s）
        区别于原"线性退避"（1s, 2s, 3s）— 指数退避更适配 akshare 反爬冷却。
        对于参数校验类错误（如"只能获取最近30天"），立即失败不重试，节省 14 秒。

        失败统一包装为 ProviderError（含 provider/method/cause），符合全库契约。
        """
        last_error: Exception | None = None
        for attempt in range(1, self.retry_count + 1):
            try:
                logger.debug("调用 AkShare 接口 %s，第 %s/%s 次尝试。", func.__name__, attempt, self.retry_count)
                return await asyncio.to_thread(func, *args, **kwargs)
            except ProviderError:
                raise  # 已是 ProviderError，直接透传
            except Exception as exc:
                last_error = exc
                # akshare 空数据 bug：原样抛 TypeError，交调用方 except TypeError 返回空表
                if self._is_akshare_empty_data_bug(exc):
                    raise
                # 不可重试错误 → 立即失败
                if self._is_non_retryable_error(exc):
                    logger.warning(
                        "AkShare 接口 %s 不可重试错误（参数/逻辑错误），跳过重试: %s",
                        func.__name__, exc,
                    )
                    raise NonRetryableProviderError(
                        f"akshare {func.__name__} 不可重试错误: {exc}",
                        provider="akshare", method=func.__name__, cause=exc,
                    ) from exc
                logger.warning(
                    "AkShare 接口 %s 调用失败，第 %s/%s 次：%s",
                    func.__name__,
                    attempt,
                    self.retry_count,
                    exc,
                )
                if attempt < self.retry_count:
                    # 指数退避：1²=1, 2²=4, 3²=9, ...
                    await asyncio.sleep(self.retry_delay_seconds * (attempt ** 2))

        logger.error("AkShare 接口 %s 最终失败。", func.__name__, exc_info=True)
        raise ProviderError(
            f"akshare {func.__name__} 调用失败（{self.retry_count} 次尝试后）",
            provider="akshare", method=func.__name__, cause=last_error,
        ) from last_error

    # ── 证券主数据 ─────────────────────────────────────────────────────

    async def get_stock_list(self) -> pd.DataFrame:
        """获取 A 股实时行情列表。"""
        self._ensure_akshare()
        try:
            logger.debug("正在获取 A 股实时行情列表...")
            df = await self._run_with_retry(ak.stock_zh_a_spot_em)
            return df
        except Exception as e:
            logger.error("获取股票列表失败: %s", e)
            raise

    async def get_stock_individual_info(self, symbol: str) -> pd.DataFrame:
        """获取个股基础信息，用于主数据表同步。"""
        self._ensure_akshare()
        try:
            logger.debug("正在获取个股基础信息, symbol=%s", symbol)
            return await self._run_with_retry(ak.stock_individual_info_em, symbol=symbol)
        except Exception as e:
            logger.error("获取个股基础信息失败, symbol=%s, error=%s", symbol, e)
            raise

    async def get_index_constituents(self, symbol: str) -> pd.DataFrame:
        """获取指数成分股列表。

        Args:
            symbol: 指数代码（如 "000300"=沪深300, "000905"=中证500, "399006"=创业板指）

        Returns:
            DataFrame，含 "品种代码" / "品种名称" / "纳入日期" 等原始列。
        """
        self._ensure_akshare()
        try:
            logger.debug("正在获取指数成分股, symbol=%s", symbol)
            return await self._run_with_retry(ak.index_stock_cons, symbol=symbol)
        except Exception as e:
            logger.error("获取指数成分股失败, symbol=%s, error=%s", symbol, e)
            raise

    async def get_stock_fundamentals(self, symbol: str) -> pd.DataFrame:
        """获取个股财务指标（PE/PB/ROE/毛利率等）。
    
        与 `get_stock_individual_info`（基础信息）严格区分：
        - individual_info: 名称/行业/总股本/流通市值（em 接口 stock_individual_info_em）
        - fundamentals:    估值 + 财务指标
    
        akshare 1.18.64: stock_zh_a_indicator 已移除，
        改用 stock_financial_analysis_indicator_em（东财财务分析指标）。
    
        Returns:
            pd.DataFrame — 字段：最新价 / 市盈率 / 市净率 / ROE / 毛利率 / 净利率 等
        """
        self._ensure_akshare()
        try:
            logger.debug("正在获取个股财务指标, symbol=%s", symbol)
            # 优先 akshare 1.18.64+ 新接口
            if hasattr(ak, "stock_financial_analysis_indicator_em"):
                return await self._run_with_retry(
                    ak.stock_financial_analysis_indicator_em, symbol=symbol,
                )
            # 兼容旧版
            if hasattr(ak, "stock_zh_a_indicator"):
                return await self._run_with_retry(ak.stock_zh_a_indicator, symbol=symbol)
            if hasattr(ak, "stock_zh_finance_indicator"):
                return await self._run_with_retry(ak.stock_zh_finance_indicator, symbol=symbol)
            logger.warning("akshare 缺少财务指标接口, 返回空 DataFrame")
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取财务指标失败, symbol=%s, error=%s", symbol, e)
            raise

    # ── 行情 ──────────────────────────────────────────────────────────

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """获取个股日线历史行情（默认前复权；""=不复权，"hfq"=后复权）。

        返回列已按全库 K 线契约归一化为 date/open/high/low/close/volume（保留其余列）。
        """
        self._ensure_akshare()
        try:
            logger.info(
                "正在获取个股日线历史行情, symbol=%s, start=%s, end=%s, adjust=%s",
                symbol,
                start_date,
                end_date,
                adjust or "none",
            )
            df = await self._run_with_retry(
                ak.stock_zh_a_hist,
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            return standardize_kline_df(df) if df is not None else df
        except Exception as e:
            logger.error("获取日线历史行情失败, symbol=%s, error=%s", symbol, e)
            raise

    async def get_minute_bars(
        self,
        symbol: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "60",
        adjust: str = "",
    ) -> pd.DataFrame:
        """获取个股分钟历史行情（返回列归一化为 date/open/high/low/close/volume）。"""
        self._ensure_akshare()
        try:
            logger.info(
                "正在获取个股分钟历史行情, symbol=%s, start=%s, end=%s, period=%s",
                symbol,
                start_datetime,
                end_datetime,
                period,
            )
            df = await self._run_with_retry(
                ak.stock_zh_a_hist_min_em,
                symbol=symbol,
                start_date=start_datetime,
                end_date=end_datetime,
                period=period,
                adjust=adjust,
            )
            return standardize_kline_df(df) if df is not None else df
        except Exception as e:
            logger.error("获取分钟历史行情失败, symbol=%s, error=%s", symbol, e)
            raise

    # ── 资金流 ────────────────────────────────────────────────────────

    async def get_market_fund_flow(self) -> pd.DataFrame:
        """获取全市场资金流向。"""
        self._ensure_akshare()
        try:
            logger.debug("正在获取全市场资金流向...")
            return await self._run_with_retry(ak.stock_market_fund_flow)
        except Exception as e:
            logger.error("获取全市场资金流向失败: %s", e)
            raise

    async def get_northbound_flow(self) -> pd.DataFrame:
        """获取北向资金汇总。"""
        self._ensure_akshare()
        try:
            logger.debug("正在获取北向资金汇总...")
            return await self._run_with_retry(ak.stock_hsgt_fund_flow_summary_em)
        except Exception as e:
            logger.error("获取北向资金汇总失败: %s", e)
            raise

    async def get_individual_fund_flow(self, symbol: str, market: str = "") -> pd.DataFrame:
        """获取个股资金流向。market 取值示例: sz/sh/bj；留空则从代码自动推断。"""
        self._ensure_akshare()
        # 自动推断 market：6开头 → sh，0/3开头 → sz，8/4开头 → bj
        if not market:
            if symbol.startswith(("6",)):
                market = "sh"
            elif symbol.startswith(("0", "3")):
                market = "sz"
            elif symbol.startswith(("8", "4")):
                market = "bj"
            else:
                market = "sz"
        try:
            logger.debug("正在获取个股资金流向, symbol=%s, market=%s", symbol, market)
            return await self._run_with_retry(
                ak.stock_individual_fund_flow,
                stock=symbol,
                market=market,
            )
        except Exception as e:
            logger.error("获取个股资金流向失败, symbol=%s, error=%s", symbol, e)
            raise

    # ── 涨停生态 ──────────────────────────────────────────────────────

    async def get_limit_up_pool(self, date: str | None = None) -> pd.DataFrame:
        """获取涨停板池数据。"""
        self._ensure_akshare()
        try:
            if not date:
                date = datetime.now().strftime("%Y%m%d")
            logger.debug("正在获取涨停板池数据, date: %s", date)
            df = await self._run_with_retry(ak.stock_zt_pool_em, date=date)
            return df
        except Exception as e:
            logger.error("获取涨停板池失败: %s", e)
            raise

    async def get_limit_down_pool(self, date: str | None = None) -> pd.DataFrame:
        """获取跌停板池数据。"""
        self._ensure_akshare()
        try:
            if not date:
                date = datetime.now().strftime("%Y%m%d")
            logger.debug("正在获取跌停板池数据, date: %s", date)
            df = await self._run_with_retry(ak.stock_zt_pool_dtgc_em, date=date)
            return df
        except Exception as e:
            logger.error("获取跌停板池失败: %s", e)
            raise

    async def get_bomb_board_pool(self, date: str | None = None) -> pd.DataFrame:
        """获取炸板池数据。"""
        self._ensure_akshare()
        try:
            if not date:
                date = datetime.now().strftime("%Y%m%d")
            logger.debug("正在获取炸板池数据, date: %s", date)
            df = await self._run_with_retry(ak.stock_zt_pool_zbgc_em, date=date)
            return df
        except Exception as e:
            logger.error("获取炸板池失败: %s", e)
            raise

    # ── 龙虎榜 ────────────────────────────────────────────────────────

    async def get_dragon_tiger_list(self, date: str | None = None) -> pd.DataFrame:
        """获取每日龙虎榜明细。date=None 时默认取当天（YYYYMMDD）。"""
        self._ensure_akshare()
        if date is None:
            date = datetime.now().strftime("%Y%m%d")
        try:
            logger.debug("正在获取 %s 龙虎榜明细...", date)
            date_formatted = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else date
            df = await self._run_with_retry(
                ak.stock_lhb_detail_em,
                start_date=date_formatted,
                end_date=date_formatted,
            )
            return df if df is not None else pd.DataFrame()
        except TypeError:
            # akshare 1.18.64 空数据 bug：非交易日/无龙虎榜时内部 None 下标崩溃。
            # 保留 exc_info 留痕：若后续 akshare 修复该 bug，此分支吞掉的是真实编码错误时仍可追溯。
            logger.warning("龙虎榜 %s 无数据（akshare 空数据异常，按空表处理）", date, exc_info=True)
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取龙虎榜失败: %s", e)
            raise

    async def get_stock_lhb(self, symbol: str, days: int = 90) -> pd.DataFrame:
        """拉指定 symbol 最近 N 日龙虎榜明细，统一为 `net_buy / famous_flag` 列。

        真实接口: `ak.stock_lhb_stock_detail_em(symbol=代码, ...)`
        失败兜底: 拉每日龙虎榜后按 symbol 过滤。

        失败语义：
        - 网络/接口错误 → raise（DataSource 走 fallback）
        - 协议成功但个股无龙虎榜 → 返空 DataFrame（合法业务结果）
        """
        self._ensure_akshare()
        if not hasattr(ak, "stock_lhb_stock_detail_em"):
            raise AttributeError("akshare 缺少 stock_lhb_stock_detail_em 接口")
        df = await self._run_with_retry(
            ak.stock_lhb_stock_detail_em,
            symbol=symbol,
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "net_buy", "famous_flag"])
        rename_map = {
            "日期": "date",
            "代码": "code",
            "名称": "name",
            "净额": "net_buy",
            "买入金额": "buy_amount",
            "卖出金额": "sell_amount",
            "涨跌幅": "change_pct",
            "上榜原因": "reason",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        try:
            # 知名游资/机构席位启发式（本地实现，替代原 features_engine 外部依赖）
            reasons = df["reason"] if "reason" in df.columns else pd.Series("", index=df.index)
            df["famous_flag"] = reasons.fillna("").map(_detect_lhb_famous_buyers)
        except Exception:
            df["famous_flag"] = False
        return df.tail(days).reset_index(drop=True)

    # ── 板块主数据 ────────────────────────────────────────────────────

    async def get_concept_board_list(self) -> pd.DataFrame:
        """获取概念板块列表。"""
        self._ensure_akshare()
        try:
            logger.debug("正在获取概念板块列表...")
            return await self._run_with_retry(ak.stock_board_concept_name_em)
        except Exception as e:
            logger.error("获取概念板块列表失败: %s", e)
            raise

    async def get_industry_board_list(self) -> pd.DataFrame:
        """获取行业板块列表。"""
        self._ensure_akshare()
        try:
            logger.debug("正在获取行业板块列表...")
            return await self._run_with_retry(ak.stock_board_industry_name_em)
        except Exception as e:
            logger.error("获取行业板块列表失败: %s", e)
            raise

    async def get_concept_board_components(self, symbol: str) -> pd.DataFrame:
        """获取概念板块成分股。symbol 为板块名称。"""
        self._ensure_akshare()
        try:
            logger.debug("正在获取概念板块成分股, symbol=%s", symbol)
            return await self._run_with_retry(ak.stock_board_concept_cons_em, symbol=symbol)
        except Exception as e:
            logger.error("获取概念板块成分股失败, symbol=%s, error=%s", symbol, e)
            raise

    async def get_industry_board_components(self, symbol: str) -> pd.DataFrame:
        """获取行业板块成分股。symbol 为板块名称。"""
        self._ensure_akshare()
        try:
            logger.debug("正在获取行业板块成分股, symbol=%s", symbol)
            return await self._run_with_retry(ak.stock_board_industry_cons_em, symbol=symbol)
        except Exception as e:
            logger.error("获取行业板块成分股失败, symbol=%s, error=%s", symbol, e)
            raise

    # ── 新闻与事件 ────────────────────────────────────────────────────

    async def get_stock_news(self, symbol: str, page_size: int = 20) -> list[dict[str, Any]]:
        """获取个股新闻，底层为东方财富新闻流。

        返回 list[dict] 统一契约（与 a_stock_data / news 的 get_stock_news 一致）：
        [{title, url, published_at, source, content}, ...]

        Args:
            symbol:     6 位股票代码（如 "000636"）
            page_size:  截取返回的最大条数（底层 ak.stock_news_em 不支持，
                        这里通过 .head(page_size) 实现）
        """
        self._ensure_akshare()
        try:
            logger.debug("正在获取个股新闻, symbol=%s, page_size=%d", symbol, page_size)
            df = await self._run_with_retry(ak.stock_news_em, symbol=symbol)
            if df is None or df.empty:
                return []
            if page_size and len(df) > page_size:
                df = df.head(page_size)
            # 统一转换为 list[dict]（字段归一化，与 a_stock_data / news 契约一致）
            records = df.to_dict(orient="records")
            return [
                {
                    "title": r.get("标题") or r.get("title") or "",
                    "url": r.get("链接") or r.get("url") or r.get("网址") or "",
                    "published_at": r.get("发布时间") or r.get("date") or r.get("时间") or "",
                    "source": r.get("来源") or r.get("media_name") or "东方财富",
                    "content": r.get("内容") or r.get("content") or "",
                }
                for r in records
            ]
        except Exception as e:
            logger.error("获取个股新闻失败, symbol=%s, error=%s", symbol, e)
            raise

    async def get_global_macro_news(
        self,
        business_date: str | None = None,
        look_back_days: int = 7,
        per_day_limit: int = 10,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取 CCTV 宏观新闻，按日期窗口聚合。

        返回 list[dict] 统一契约（与 news.get_global_news 一致）：
        [{title, content, published_at, url, source}, ...]

        Args:
            business_date: ISO 日期字符串，None 时默认取当天。
            look_back_days: 回看天数。
            per_day_limit: 每日条数上限；兼容调用方传 limit= 关键字（覆盖）。
        """
        limit = kwargs.pop("limit", None)
        if limit is not None:
            per_day_limit = int(limit)
        self._ensure_akshare()
        if business_date is None:
            business_date = datetime.now().strftime("%Y-%m-%d")
        try:
            anchor_date = datetime.strptime(business_date, "%Y-%m-%d")
            logger.info(
                "正在获取宏观新闻, business_date=%s, look_back_days=%s",
                business_date,
                look_back_days,
            )
            items: list[dict[str, Any]] = []
            for offset in range(max(1, look_back_days)):
                current_date = (anchor_date - timedelta(days=offset)).strftime("%Y%m%d")
                df = await self._run_with_retry(ak.news_cctv, date=current_date)
                if df is not None and not df.empty:
                    for _, row in df.head(per_day_limit).iterrows():
                        title = str(row.get("标题") or row.get("title") or "").strip()
                        if not title:
                            continue
                        items.append({
                            "title": title,
                            "content": str(row.get("内容") or row.get("content") or "").strip(),
                            "published_at": str(row.get("日期") or row.get("date") or ""),
                            "url": str(row.get("链接") or row.get("url") or "").strip(),
                            "source": "央视新闻",
                        })
            return items
        except Exception as e:
            logger.error("获取宏观新闻失败, business_date=%s, error=%s", business_date, e)
            raise

    async def get_global_news_aggregated(
        self,
        business_date: date | str | None = None,
        look_back_days: int = 1,
        per_source_limit: int = 20,
    ) -> list[dict[str, Any]]:
        """聚合多源宏观/全球快讯为标准化 dict 列表，供 `EventIngestJob` 写入 `fact_event_raw`。"""
        self._ensure_akshare()
        if business_date is None:
            business_date = date.today()
        if isinstance(business_date, date):
            anchor_str = business_date.strftime("%Y-%m-%d")
        else:
            anchor_str = str(business_date)[:10]
        anchor_dt = datetime.strptime(anchor_str, "%Y-%m-%d")

        results: list[dict[str, Any]] = []
        seen_titles: set[str] = set()

        source_specs: list[tuple[str, Callable[..., Any], dict[str, Any]]] = []
        if hasattr(ak, "stock_telegraph_cls"):
            source_specs.append(("财联社电报", ak.stock_telegraph_cls, {}))
        if hasattr(ak, "news_economic_baidu"):
            source_specs.append(("百度宏观", ak.news_economic_baidu, {"date": anchor_str}))
        if hasattr(ak, "news_cctv"):
            for offset in range(max(1, look_back_days)):
                day_str = (anchor_dt - timedelta(days=offset)).strftime("%Y%m%d")
                source_specs.append(("央视新闻", ak.news_cctv, {"date": day_str}))

        for source_name, func, kwargs in source_specs:
            try:
                df = await self._run_with_retry(func, **kwargs)
            except Exception as exc:
                logger.warning("聚合 %s 新闻失败: %s", source_name, exc)
                continue
            if df is None:
                continue
            try:
                frame = df if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
            except Exception as exc:
                logger.debug("新闻源 %s 转 DataFrame 失败，跳过: %s", source_name, exc)
                continue
            if frame.empty:
                continue

            for _, row in frame.head(per_source_limit).iterrows():
                title = (
                    str(row.get("标题") or row.get("title") or row.get("headline") or "")
                    .strip()
                )
                if not title or title in seen_titles:
                    continue
                summary = (
                    str(
                        row.get("内容")
                        or row.get("summary")
                        or row.get("content")
                        or ""
                    ).strip()
                )
                url_val = (
                    row.get("网址") or row.get("url") or row.get("link")
                )
                url = str(url_val).strip() if url_val not in (None, "") else None
                published_at = anchor_dt
                for k in ("发布时间", "published_at", "date", "publish_time"):
                    if k in row and row[k] not in (None, ""):
                        try:
                            published_at = pd.to_datetime(row[k]).to_pydatetime()
                            break
                        except (ValueError, TypeError) as exc:
                            logger.debug("新闻发布时间解析失败, key=%s value=%s: %s", k, row.get(k), exc)
                            published_at = anchor_dt

                seen_titles.add(title)
                results.append(
                    {
                        "title": title,
                        "summary": summary or None,
                        "url": url,
                        "published_at": published_at,
                        "source": source_name,
                    }
                )

        logger.info(
            "聚合全球快讯完成, business_date=%s, count=%d, sources=%d",
            anchor_str,
            len(results),
            len(source_specs),
        )
        return results

    # ── 业绩预告 / 一致预期 / 研报 / 监管处罚（P0 补强） ────────────

    async def get_earnings_forecast(self, symbol: str) -> pd.DataFrame:
        """个股业绩预告（东方财富）。

        用于在财报季前提前识别"预增 / 预减 / 扭亏 / 续亏"信号，
        StockAnalyst 战术判定时与 EPS surprise 一起纳入考量。

        Args:
            symbol: 6 位股票代码（如 "000636"）

        Returns:
            pd.DataFrame — 字段含：股票代码、名称、预告类型、预告净利润
                变动幅度、业绩变动原因、预告日期、报告期等
        """
        self._ensure_akshare()
        try:
            logger.debug("正在获取业绩预告, symbol=%s", symbol)
            if not hasattr(ak, "stock_yjyg_em"):
                logger.warning("akshare 缺少 stock_yjyg_em, 返回空 DataFrame")
                return pd.DataFrame()
            # akshare 1.18.64: stock_yjyg_em(date) 无 symbol 参数
            # → 拉当日全市场预告后按股票代码过滤
            today = datetime.now().strftime("%Y%m%d")
            df = await self._run_with_retry(ak.stock_yjyg_em, date=today)
            if df is None or df.empty:
                return pd.DataFrame()
            return _filter_by_symbol(df, symbol)
        except TypeError:
            # akshare 1.18.64 空数据 bug：非财报季日期无预告时内部 None 下标崩溃。
            # 保留 exc_info 留痕：若后续 akshare 修复该 bug，此分支吞掉的是真实编码错误时仍可追溯。
            logger.warning("业绩预告 %s 无数据（akshare 空数据异常，按空表处理）", symbol, exc_info=True)
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取业绩预告失败, symbol=%s, error=%s", symbol, e)
            raise

    async def get_analyst_consensus(self, symbol: str) -> pd.DataFrame:
        """卖方一致预期（东方财富 / 新浪汇总）。

        含：分析师数、买入/增持/中性/减持评级家数、近一年 EPS 预测均值/极值、
        目标价均值等。LLM 推理时作为"市场预期锚点"使用。

        Args:
            symbol: 6 位股票代码

        Returns:
            pd.DataFrame — 一行多列的综合评级与预测快照
        """
        self._ensure_akshare()
        try:
            logger.debug("正在获取一致预期, symbol=%s", symbol)
            # akshare 1.18.64: stock_analyst_rank_em(year) 无 symbol 参数
            # → 拉全市场年度榜单后按股票代码过滤
            if hasattr(ak, "stock_analyst_rank_em"):
                df = await self._run_with_retry(ak.stock_analyst_rank_em, year=str(datetime.now().year))
            elif hasattr(ak, "stock_zh_analyst_rank_em"):
                df = await self._run_with_retry(ak.stock_zh_analyst_rank_em)
            else:
                logger.warning("akshare 缺少 analyst_rank 接口, 返回空 DataFrame")
                return pd.DataFrame()
            if df is None or df.empty:
                return pd.DataFrame()
            return _filter_by_symbol(df, symbol)
        except Exception as e:
            logger.error("获取一致预期失败, symbol=%s, error=%s", symbol, e)
            raise

    async def get_research_reports(
        self,
        symbol: str | None = None,
        industry: str | None = None,
        page_size: int = 30,
    ) -> list[dict[str, Any]]:
        """卖方研报流（东方财富研报中心）。

        用于 LLM 板块/个股推理时了解主流卖方观点。
        列表契约（避免 DataFrame ambiguous truth）。

        Args:
            symbol:    个股代码（与 industry 互斥）
            industry:  行业名（如 "新能源" / "半导体"）
            page_size: 返回条数上限

        Returns:
            list[dict]，每条含：title / org / researcher / rating /
            report_date / industry / url
        """
        self._ensure_akshare()
        try:
            if not hasattr(ak, "stock_research_report_em"):
                logger.warning("akshare 缺少 stock_research_report_em, 返回 []")
                return []
            # akshare 接口只支持 symbol 参数
            target = symbol or industry
            if not target:
                raise ValueError("必须传 symbol 或 industry 之一")
            logger.debug("正在获取研报流, target=%s", target)
            df = await self._run_with_retry(ak.stock_research_report_em, symbol=target)
            if df is None or df.empty:
                return []
            if page_size and len(df) > page_size:
                df = df.head(page_size)
            # DataFrame → list[dict]
            records = df.to_dict(orient="records")
            return [
                {
                    "title": r.get("标题") or r.get("title") or "",
                    "org": r.get("机构") or r.get("org") or "",
                    "researcher": r.get("研究员") or r.get("researcher") or "",
                    "rating": r.get("评级") or r.get("rating") or "",
                    "report_date": str(r.get("日期") or r.get("date") or ""),
                    "industry": r.get("行业") or r.get("industry") or "",
                }
                for r in records
            ]
        except Exception as e:
            logger.error("获取研报流失败, target=%s, error=%s", symbol or industry, e)
            raise

    async def get_regulatory_penalties(self, symbol: str) -> list[dict[str, Any]]:
        """监管处罚 / 问询函（东方财富监管公告）。

        命中"立案调查 / 处罚 / 问询"等是战术判定的硬约束之一
        （hard_block 候选）。列表契约便于 LLM 处理。

        Args:
            symbol: 6 位股票代码

        Returns:
            list[dict]，每条含：title / type / publish_date / source / url
        """
        self._ensure_akshare()
        try:
            logger.debug("正在获取监管处罚, symbol=%s", symbol)
            # akshare 1.18.64: stock_zfgg_em 已移除，仅剩 stock_xgsglb_em(symbol="全部股票")
            # → 拉全部后按代码过滤（symbol 传 6 位代码会 KeyError）
            if hasattr(ak, "stock_zfgg_em"):
                df = await self._run_with_retry(ak.stock_zfgg_em, symbol="全部股票")
            elif hasattr(ak, "stock_xgsglb_em"):
                df = await self._run_with_retry(ak.stock_xgsglb_em, symbol="全部股票")
            else:
                logger.warning("akshare 缺少 stock_zfgg_em / stock_xgsglb_em, 返回 []")
                return []
            if df is None or df.empty:
                return []
            df = _filter_by_symbol(df, symbol)
            if df.empty:
                return []
            records = df.to_dict(orient="records")
            return [
                {
                    "title": r.get("标题") or r.get("title") or "",
                    "type": r.get("类型") or r.get("type") or "",
                    "publish_date": str(r.get("发布日期") or r.get("date") or ""),
                    "source": r.get("来源") or r.get("source") or "证监会",
                }
                for r in records
            ]
        except Exception as e:
            logger.error("获取监管处罚失败, symbol=%s, error=%s", symbol, e)
            raise

    # ── 宏观数据（Macro 补强） ──────────────────────────────────────

    async def get_macro_pmi(self) -> pd.DataFrame:
        """中国 PMI（采购经理指数）历史。

        用于市场层 `market_regime` 推断：PMI 连续低于荣枯线（50）
        → risk_off_contraction 候选。

        Returns:
            pd.DataFrame — 字段：月份 / 制造业 PMI / 非制造业 PMI 等
        """
        self._ensure_akshare()
        try:
            if hasattr(ak, "macro_china_pmi_yearly"):
                return await self._run_with_retry(ak.macro_china_pmi_yearly)
            if hasattr(ak, "macro_china_pmi"):
                return await self._run_with_retry(ak.macro_china_pmi)
            logger.warning("akshare 缺少 PMI 接口, 返回空 DataFrame")
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取 PMI 失败: %s", e)
            raise

    async def get_macro_cpi(self) -> pd.DataFrame:
        """中国 CPI（消费者价格指数）历史。

        CPI 同比 > 3% 触发通胀警惕；连续下行 → 需求疲弱。

        Returns:
            pd.DataFrame — 字段：月份 / CPI 同比 / CPI 环比
        """
        self._ensure_akshare()
        try:
            if hasattr(ak, "macro_china_cpi_yearly"):
                return await self._run_with_retry(ak.macro_china_cpi_yearly)
            if hasattr(ak, "macro_china_cpi"):
                return await self._run_with_retry(ak.macro_china_cpi)
            logger.warning("akshare 缺少 CPI 接口, 返回空 DataFrame")
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取 CPI 失败: %s", e)
            raise

    async def get_macro_m2(self) -> pd.DataFrame:
        """中国 M2（广义货币供应）历史。

        M2 同比 - GDP 同比 - CPI 同比 ≈ 实际货币缺口，
        持续走高 → 流动性宽松，risk_on 候选。

        Returns:
            pd.DataFrame — 字段：月份 / M2 同比 / 货币和准货币
        """
        self._ensure_akshare()
        try:
            if hasattr(ak, "macro_china_m2_yearly"):
                return await self._run_with_retry(ak.macro_china_m2_yearly)
            if hasattr(ak, "macro_china_m2"):
                return await self._run_with_retry(ak.macro_china_m2)
            logger.warning("akshare 缺少 M2 接口, 返回空 DataFrame")
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取 M2 失败: %s", e)
            raise

    async def get_macro_bond_yield(self) -> pd.DataFrame:
        """中国 10Y 国债收益率（及中美国债利差）。

        中美国债利差倒挂 → 北向资金外流压力；纯 10Y 走势 → 流动性偏好。

        Returns:
            pd.DataFrame — 字段：日期 / 中国国债收益率 / 美国国债收益率
        """
        self._ensure_akshare()
        try:
            if hasattr(ak, "bond_zh_us_rate"):
                # 限制数据量：只拉最近 6 个月，避免全量 9000+ 行拖慢响应
                from datetime import datetime, timedelta
                start_date = (datetime.now() - timedelta(days=180)).strftime("%Y%m%d")
                return await self._run_with_retry(ak.bond_zh_us_rate, start_date=start_date)
            if hasattr(ak, "bond_zh_rate"):
                # 老接口：仅中国国债
                return await self._run_with_retry(ak.bond_zh_rate)
            logger.warning("akshare 缺少 bond_zh_us_rate 接口, 返回空 DataFrame")
            return pd.DataFrame()
        except Exception as e:
            logger.error("获取国债收益率失败: %s", e)
            raise

    # ── 板块层数据 (sector_feature_sync 链路) ─────────────────────────

    async def get_sector_recent_bars(self, sector_name: str, days: int = 30) -> pd.DataFrame:
        """拉板块指数近 N 日 K 线，统一为 `turnover / pct_change` 列。

        失败语义：网络/接口错误 → raise；成功但板块无 K 线 → 返空 DataFrame。
        akshare 1.18.64+：stock_board_industry_index_em 已移除，改用 stock_board_industry_hist_em。
        """
        self._ensure_akshare()
        # 优先新接口 stock_board_industry_hist_em，兼容旧版 stock_board_industry_index_em
        if hasattr(ak, "stock_board_industry_hist_em"):
            from datetime import datetime, timedelta
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
            df = await self._run_with_retry(
                ak.stock_board_industry_hist_em,
                symbol=sector_name, period="日k",
                start_date=start_date, end_date=end_date, adjust="",
            )
        elif hasattr(ak, "stock_board_industry_index_em"):
            df = await self._run_with_retry(
                ak.stock_board_industry_index_em,
                symbol=sector_name, period="日k",
            )
        else:
            logger.warning("akshare 缺少板块 K 线接口 (hist_em / index_em), 返回空 DataFrame")
            return pd.DataFrame(columns=["date", "close", "turnover", "pct_change"])
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "close", "turnover", "pct_change"])
        rename_map = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "turnover",
            "涨跌幅": "pct_change",
            "涨跌额": "change",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if "pct_change" not in df.columns and "change" in df.columns:
            df["pct_change"] = pd.to_numeric(df["change"], errors="coerce")
        return df.tail(days).reset_index(drop=True)

    async def get_sector_fund_flow(self, sector_name: str) -> pd.DataFrame:
        """拉板块资金流，统一为 `sector / main_net_inflow / net_inflow` 等列。

        失败语义：网络/接口错误 → raise ProviderError；成功但板块无资金流 → 返空 DataFrame。
        双源策略：东财（stock_sector_fund_flow_hist）优先，
        网络失败自动降级同花顺（stock_board_industry_summary_ths，真独立源）。
        注：降级后的空结果由路由层质量语义处理（selector 类方法空=失败，会继续 fallback）。
        """
        self._ensure_akshare()
        # 东财优先（新接口 → 旧接口）
        if hasattr(ak, "stock_sector_fund_flow_hist") or hasattr(ak, "stock_board_industry_fund_flow_em"):
            try:
                if hasattr(ak, "stock_sector_fund_flow_hist"):
                    df = await self._run_with_retry(
                        ak.stock_sector_fund_flow_hist, symbol=sector_name,
                    )
                else:
                    df = await self._run_with_retry(
                        ak.stock_board_industry_fund_flow_em, symbol=sector_name,
                    )
                if df is not None and not df.empty:
                    rename_map = {
                        "主力净流入-净额": "main_net_inflow",  # 东财板块历史资金流的真实列名
                        "主力净流入": "main_net_inflow",
                        "净流入": "net_inflow",
                        "净额": "net_inflow",
                        "超大单净额": "super_large_net",
                        "大单净额": "large_net",
                        "中单净额": "mid_net",
                        "小单净额": "small_net",
                    }
                    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
                    df = df.reset_index(drop=True)
                    if "sector" not in df.columns:
                        df.insert(0, "sector", sector_name)
                    return df
                logger.warning("板块资金流(东财)为空，尝试同花顺降级: %s", sector_name)
            except NonRetryableProviderError:
                raise  # 参数/逻辑错误：重试与降级均无意义，快速失败
            except Exception as exc:  # noqa: BLE001
                logger.warning("板块资金流(东财)失败，降级同花顺: %s", exc)
        # 降级：同花顺（真独立源，不依赖东财）
        try:
            return await self.get_sector_fund_flow_ths(sector_name)
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"akshare 板块资金流接口不可用/调用失败: {exc}",
                provider="akshare", method="get_sector_fund_flow", cause=exc,
            ) from exc

    async def get_sector_fund_flow_ths(self, sector_name: str = "") -> pd.DataFrame:
        """拉板块资金流（同花顺口径，不依赖东财 — 真独立源）。

        同花顺 `stock_board_industry_summary_ths` 返回 90 个行业板块，
        含 涨跌幅/净流入/上涨下跌家数/领涨股。

        Args:
            sector_name: 板块名过滤（子串匹配）；空=返回全部

        Returns:
            pd.DataFrame，列与 get_sector_fund_flow 对齐：
            sector / main_net_inflow / net_inflow / pct_change / amount /
            up_count / down_count / lead_name
        """
        self._ensure_akshare()
        if not hasattr(ak, "stock_board_industry_summary_ths"):
            raise AttributeError("akshare 缺少 stock_board_industry_summary_ths 接口")
        df = await self._run_with_retry(ak.stock_board_industry_summary_ths)
        if df is None or df.empty:
            return pd.DataFrame(
                columns=["sector", "main_net_inflow", "net_inflow", "pct_change",
                         "amount", "up_count", "down_count", "lead_name"]
            )
        rename_map = {
            "板块": "sector",
            "涨跌幅": "pct_change",
            "净流入": "net_inflow",
            "总成交额": "amount",
            "上涨家数": "up_count",
            "下跌家数": "down_count",
            "领涨股": "lead_name",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        cols = ["sector", "main_net_inflow", "net_inflow", "pct_change", "amount", "up_count", "down_count", "lead_name"]
        for c in cols:
            if c not in df.columns:
                df[c] = None
        if sector_name:
            df = df[df["sector"].astype(str).str.contains(sector_name, na=False)]
        return df[cols].reset_index(drop=True)

    async def get_sector_leaders(self, sector_name: str, top_n: int = 5) -> pd.DataFrame:
        """拉板块领涨股，统一为 `code / name / change_pct` 列。

        失败语义：网络/接口错误 → raise；成功但板块无成分股 → 返空 DataFrame。
        """
        self._ensure_akshare()
        if not hasattr(ak, "stock_board_industry_cons_em"):
            raise AttributeError("akshare 缺少 stock_board_industry_cons_em 接口")
        df = await self._run_with_retry(
            ak.stock_board_industry_cons_em,
            symbol=sector_name,
        )
        if df is None or df.empty:
            return pd.DataFrame(columns=["code", "name", "change_pct"])
        rename_map = {
            "代码": "code",
            "名称": "name",
            "涨跌幅": "change_pct",
            "最新价": "price",
            "主力净流入": "main_net_inflow",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        if "change_pct" in df.columns:
            df = df.sort_values("change_pct", ascending=False).head(top_n)
        return df.reset_index(drop=True)

    async def get_sector_event_signals(self, sector_name: str) -> list[dict[str, Any]]:
        """拉板块相关事件信号，返回 list[dict]（title/source/published_at/url）。

        失败语义：网络/接口错误 → raise；成功但板块无事件 → 返 []。
        akshare 1.18.64+：stock_board_industry_news_em 已移除，尝试 stock_news_em 兜底。
        """
        self._ensure_akshare()
        if hasattr(ak, "stock_board_industry_news_em"):
            df = await self._run_with_retry(
                ak.stock_board_industry_news_em, symbol=sector_name,
            )
        else:
            logger.warning("akshare 缺少 stock_board_industry_news_em 接口, 返回空事件列表")
            return []
        if df is None or df.empty:
            return []
        events: list[dict[str, Any]] = []
        for _, row in df.head(20).iterrows():
            title = str(row.get("标题") or row.get("title") or "").strip()
            if not title:
                continue
            events.append(
                {
                    "title": title,
                    "summary": str(row.get("内容") or row.get("summary") or "").strip() or None,
                    "url": str(row.get("网址") or row.get("url") or "").strip() or None,
                    "published_at": row.get("发布时间") or row.get("date") or None,
                    "source": "akshare_industry_news",
                }
            )
        return events

    # ── 财务报表（已迁出）────────────────────────────────────
    # 财务三表（balance_sheet / cashflow_statement / income_statement）
    # 在 akshare 1.18.64 中上游接口损坏（`soup.find(attrs={"id": "hidctype"})` 返 None）。
    # 改走 financial_report provider（底层 = 东财数据中心），端点签名保持不变。

    async def get_insider_transactions(self, symbol: str) -> pd.DataFrame:
        """A 股没有 SEC 式内幕交易披露 — 显式 raise 以保持契约清晰。

        上层若用 `get_insider_transactions` 拿"占位 DataFrame"会违反"数据真实可靠"。
        正确做法：调用方先检查 SUPPORTED_INTRINSIC_ENDPOINTS 决定是否走该端点。
        """
        raise NotImplementedError(
            f"A 股暂无 SEC 式公开内幕交易披露接口（symbol={symbol}）；"
            "请改用 'get_regulatory_penalties'（监管公告） 或 'get_dragon_tiger'（龙虎榜）作为替代信号源"
        )
