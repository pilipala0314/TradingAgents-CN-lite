"""Baostock Provider — 免费稳定的 A 股历史数据源。

特点：
- 完全免费，无需注册/Token
- 数据稳定（证券宝官方维护，非爬虫）
- 覆盖：日/周/月/分钟 K 线、基本面指标、行业分类、交易日历
- 局限：无实时行情（仅历史）、无资金流、数据更新有 1 天延迟
- 并发模型：baostock 基于单一全局 TCP 连接，协议非线程安全，
  本 provider 用进程级锁串行化所有调用（登录+查询原子化）

适用场景：
- 回测历史 K 线（akshare 不稳定时的可靠 fallback）
- 基本面指标查询（PE/PB/ROE/毛利率等）
- 行业分类与成分股

安装：pip install baostock
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from datasource.bootstrap import ensure_module_or_install
from datasource.errors import ProviderError
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

# baostock 基于单一全局 socket 连接，协议非线程安全：
# 登录 + 查询必须整体串行化，否则并发请求会交错收发导致数据错乱。
_bs_lock = threading.Lock()
_logged_in = False

# 统一复权值域（与 akshare/tushare 对齐）：qfq/hfq/"" → baostock adjustflag 2/1/3
_ADJUST_MAP = {"qfq": "2", "hfq": "1", "": "3", "none": "3"}


def _ensure_baostock():
    """确保 baostock 已安装。"""
    return ensure_module_or_install("baostock", provider="baostock")


def _sync_login(bs) -> None:
    """同步登录（必须在 _bs_lock 内调用）。"""
    global _logged_in
    if not _logged_in:
        result = bs.login()
        if result.error_code != "0":
            raise ProviderError(
                f"baostock 登录失败: {result.error_msg}",
                provider="baostock",
            )
        _logged_in = True


def _query_to_df(bs, query: Callable[[], Any]) -> pd.DataFrame:
    """在锁内执行 baostock 查询（登录+查询原子化），逐行收集为 DataFrame。

    失败时重置登录态，允许下次调用重新登录（连接可能已断）。
    """
    global _logged_in
    with _bs_lock:
        try:
            _sync_login(bs)
            rs = query()
            rows = []
            while rs.error_code == "0" and rs.next():
                rows.append(rs.get_row_data())
            if not rows:
                return pd.DataFrame()
            return pd.DataFrame(rows, columns=rs.fields)
        except Exception:
            _logged_in = False
            raise


async def _run_query(fn: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """在线程池中执行 baostock 查询并施加 30s 超时。

    baostock 基于单一全局 TCP 连接，连接假死时 rs.next() 可能无限阻塞，
    必须外套超时避免事件循环被拖死。超时抛 asyncio.TimeoutError，
    由调用方的 except Exception 统一包装为 ProviderError（与失败语义一致）。
    """
    return await asyncio.wait_for(asyncio.to_thread(fn), timeout=30)


def _to_baostock_symbol(symbol: str) -> str:
    """将 6 位代码转为 baostock 格式（sh.600519 / sz.000001）。

    支持的输入格式：
    - "600519" / "sh600519" / "SH600519"（前缀形式）
    - "600519.SH" / "600519.sh"（tushare/akshare 通用后缀格式）
    - "sh.600519"（baostock 原生格式，规范化返回）
    """
    s = symbol.strip()
    up = s.upper()
    if "." in up:
        left, _, right = up.partition(".")
        # 原生格式 "sh.600519" / 后缀格式 "600519.SH"
        if right.isdigit() and left in ("SH", "SZ", "BJ"):
            return f"{left.lower()}.{right}"
        if left.isdigit() and right in ("SH", "SZ", "BJ"):
            return f"{right.lower()}.{left}"
    # 前缀格式 "SH600519"
    if up.startswith(("SH", "SZ", "BJ")):
        up = up[2:]
    # 裸 6 位代码，按首位映射市场
    if up.startswith(("6", "9")):
        return f"sh.{up}"
    if up.startswith(("0", "2", "3")):
        return f"sz.{up}"
    if up.startswith(("4", "8")):
        return f"bj.{up}"
    return f"sh.{up}"


@register_provider("baostock")
class BaostockProvider:
    """证券宝 Provider — 免费稳定的历史数据源。"""

    @property
    def name(self) -> str:
        return "baostock"

    async def health_check(self) -> bool:
        try:
            _ensure_baostock()
            return True
        except ProviderError:
            return False

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "baostock" in provs)

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """拉取日 K 线（默认前复权）。

        Args:
            symbol: 6 位股票代码
            start_date: YYYYMMDD 或 YYYY-MM-DD
            end_date: YYYYMMDD 或 YYYY-MM-DD
            adjust: 统一值域 "qfq"=前复权 "hfq"=后复权 ""=不复权；
                    兼容旧值域 "1"/"2"/"3"
        """
        bs = _ensure_baostock()
        bs_symbol = _to_baostock_symbol(symbol)
        adjustflag = _ADJUST_MAP.get(adjust, adjust)
        # 标准化日期格式
        sd = start_date.replace("-", "") if start_date else "20200101"
        ed = end_date.replace("-", "") if end_date else date.today().strftime("%Y%m%d")
        # baostock 日期格式：YYYY-MM-DD
        sd_fmt = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
        ed_fmt = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"

        def _fetch() -> pd.DataFrame:
            df = _query_to_df(bs, lambda: bs.query_history_k_data_plus(
                bs_symbol,
                "date,code,open,high,low,close,volume,amount,turn,pctChg",
                start_date=sd_fmt,
                end_date=ed_fmt,
                frequency="d",
                adjustflag=adjustflag,
            ))
            if df.empty:
                return df
            # 类型转换
            for col in ["open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df

        try:
            return await _run_query(_fetch)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"baostock get_daily_bars 失败: {exc}",
                provider="baostock", method="get_daily_bars", cause=exc,
            ) from exc

    async def get_stock_fundamentals(
        self,
        symbol: str,
        year: int | None = None,
        quarter: int | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """拉取基本面指标（盈利能力：ROE/净利率/毛利率/EPS 等）。

        Args:
            symbol: 6 位股票代码
            year: 年份（默认最近）
            quarter: 季度 1-4（默认最近）

        底层接口：baostock query_profit_data（按报告期）。
        返回列：code/pubDate/statDate/roeAvg/npMargin/gpMargin/netProfit/epsTTM 等。
        """
        bs = _ensure_baostock()
        bs_symbol = _to_baostock_symbol(symbol)
        # 默认取最近一个已披露季度：按当前月推算最近已结束季度（1-3月=Q1... 10-12月=Q4），
        # 1 月时回退到上年 Q4（Q4 财报通常 1 月后才披露）
        if year is None or quarter is None:
            now = date.today()
            year = now.year
            quarter = (now.month - 1) // 3 + 1
            if now.month == 1:
                year -= 1
                quarter = 4

        def _fetch() -> pd.DataFrame:
            return _query_to_df(bs, lambda: bs.query_profit_data(code=bs_symbol, year=year, quarter=quarter))

        try:
            return await _run_query(_fetch)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"baostock get_stock_fundamentals 失败: {exc}",
                provider="baostock", method="get_stock_fundamentals", cause=exc,
            ) from exc

    async def get_industry_classification(self, **kwargs: Any) -> pd.DataFrame:
        """获取申万行业分类。"""
        bs = _ensure_baostock()

        def _fetch() -> pd.DataFrame:
            return _query_to_df(bs, bs.query_stock_industry)

        try:
            return await _run_query(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"baostock get_industry_classification 失败: {exc}",
                provider="baostock", method="get_industry_classification", cause=exc,
            ) from exc

    async def get_trade_dates(
        self,
        start_date: str = "20200101",
        end_date: str = "",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """获取交易日历。"""
        bs = _ensure_baostock()
        sd = start_date.replace("-", "")
        ed = end_date.replace("-", "") if end_date else date.today().strftime("%Y%m%d")
        sd_fmt = f"{sd[:4]}-{sd[4:6]}-{sd[6:]}"
        ed_fmt = f"{ed[:4]}-{ed[4:6]}-{ed[6:]}"

        def _fetch() -> pd.DataFrame:
            return _query_to_df(bs, lambda: bs.query_trade_dates(start_date=sd_fmt, end_date=ed_fmt))

        try:
            return await _run_query(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"baostock get_trade_dates 失败: {exc}",
                provider="baostock", method="get_trade_dates", cause=exc,
            ) from exc
