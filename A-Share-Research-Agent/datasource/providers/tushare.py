"""Tushare Provider — 专业级金融数据接口。

特点：
- 数据全面：行情/基本面/资金流/宏观/指数/期货/港股/美股
- 数据质量高：机构级数据清洗
- 稳定可靠：官方 API 服务（非爬虫）
- 需要 Token：免费注册获取（https://tushare.pro/register）
- 积分制：基础接口免费，高级接口需积分

适用场景：
- akshare 不稳定时的首选 fallback
- 需要高质量财务数据
- 需要资金流/融资融券/龙虎榜等进阶数据

配置：
    环境变量 TUSHARE_TOKEN=你的token
    或 .env 文件中 TUSHARE_TOKEN=xxx

安装：pip install tushare
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import date
from typing import Any

import pandas as pd

from datasource.bootstrap import ensure_module_or_install
from datasource.config import get_datasource_config
from datasource.errors import ProviderError
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

# 全局 tushare pro api 实例
_ts_api = None


def _get_api():
    """获取 tushare pro api 实例（懒加载）。"""
    global _ts_api
    if _ts_api is not None:
        return _ts_api
    ts = ensure_module_or_install("tushare", provider="tushare")

    token = get_datasource_config().tushare_token or os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise ProviderError(
            "TUSHARE_TOKEN 未配置，请在 .env 或环境变量中设置",
            provider="tushare",
        )
    ts.set_token(token)
    _ts_api = ts.pro_api()
    return _ts_api


def _to_ts_symbol(symbol: str) -> str:
    """将 6 位代码转为 tushare 格式（600519.SH / 000001.SZ）。"""
    symbol = symbol.strip().split(".")[0]
    if symbol.startswith(("6", "9")):
        return f"{symbol}.SH"
    elif symbol.startswith(("0", "2", "3")):
        return f"{symbol}.SZ"
    elif symbol.startswith(("4", "8")):
        return f"{symbol}.BJ"
    return f"{symbol}.SH"


async def _run_query(fn: Callable[[], Any]) -> Any:
    """在线程池中执行 tushare 查询并施加 30s 超时。

    tushare SDK 内部 requests 调用不暴露超时，连接假死时可能无限阻塞，
    外套超时避免拖死事件循环；超时抛 asyncio.TimeoutError，
    由调用方现有 except 统一包装为 ProviderError（保持异常包装现状）。
    """
    return await asyncio.wait_for(asyncio.to_thread(fn), timeout=30)


@register_provider("tushare")
class TushareProvider:
    """Tushare Pro Provider — 专业级金融数据。"""

    @property
    def name(self) -> str:
        return "tushare"

    async def health_check(self) -> bool:
        try:
            _get_api()
            return True
        except ProviderError:
            return False

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "tushare" in provs)

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str = "",
        end_date: str = "",
        adjust: str = "qfq",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """拉取日 K 线（qfq/hfq 走 pro_bar 真复权，不复权走 daily）。

        Args:
            symbol: 6 位股票代码
            start_date: YYYYMMDD
            end_date: YYYYMMDD
            adjust: qfq=前复权 hfq=后复权 空=不复权

        Raises:
            ProviderError: 复权接口调用失败（通常为积分不足），明确提示而非静默返回不复权数据。
        """
        api = _get_api()
        ts_code = _to_ts_symbol(symbol)
        sd = start_date.replace("-", "") or "20200101"
        ed = end_date.replace("-", "") or date.today().strftime("%Y%m%d")

        def _fetch() -> pd.DataFrame:
            if adjust in ("qfq", "hfq"):
                try:
                    # pro_bar 按复权因子计算，返回真实复权价格
                    df = api.pro_bar(ts_code=ts_code, start_date=sd, end_date=ed, adj=adjust)
                except Exception as exc:
                    raise ProviderError(
                        f"tushare 复权数据获取失败（adjust={adjust}，可能需要更高积分）: {exc}",
                        provider="tushare", method="get_daily_bars", cause=exc,
                    ) from exc
            else:
                df = api.daily(ts_code=ts_code, start_date=sd, end_date=ed)
            if df is None or df.empty:
                return pd.DataFrame()
            # 标准化列名
            rename = {
                "trade_date": "date", "ts_code": "code",
                "vol": "volume", "pct_chg": "pctChg",
            }
            df = df.rename(columns=rename)
            df = df.sort_values("date").reset_index(drop=True)
            return df

        try:
            return await _run_query(_fetch)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"tushare get_daily_bars 失败: {exc}",
                provider="tushare", method="get_daily_bars", cause=exc,
            ) from exc

    async def get_stock_list(self, **kwargs: Any) -> pd.DataFrame:
        """获取 A 股股票列表。"""
        api = _get_api()

        def _fetch() -> pd.DataFrame:
            df = api.stock_basic(
                exchange="", list_status="L",
                fields="ts_code,symbol,name,area,industry,market,list_date",
            )
            return df if df is not None else pd.DataFrame()

        try:
            return await _run_query(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"tushare get_stock_list 失败: {exc}",
                provider="tushare", method="get_stock_list", cause=exc,
            ) from exc

    async def get_stock_fundamentals(
        self, symbol: str, **kwargs: Any,
    ) -> pd.DataFrame:
        """获取个股基本面指标（PE/PB/总市值/流通市值等）。"""
        api = _get_api()
        ts_code = _to_ts_symbol(symbol)

        def _fetch() -> pd.DataFrame:
            df = api.daily_basic(
                ts_code=ts_code,
                fields="ts_code,trade_date,pe,pb,ps,total_mv,circ_mv,turnover_rate,volume_ratio",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            # 显式按交易日降序取最新，不依赖接口默认排序（接口排序变化会取到旧数据）
            if "trade_date" in df.columns:
                df = df.sort_values("trade_date", ascending=False)
            return df.head(1)  # 最新一条

        try:
            return await _run_query(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"tushare get_stock_fundamentals 失败: {exc}",
                provider="tushare", method="get_stock_fundamentals", cause=exc,
            ) from exc

    async def get_market_fund_flow(self, **kwargs: Any) -> pd.DataFrame:
        """获取全市场资金流向（主力/超大单/大单/中单/小单）。"""
        api = _get_api()

        def _fetch() -> pd.DataFrame:
            today = date.today().strftime("%Y%m%d")
            df = api.moneyflow_hsgt(trade_date=today)
            return df if df is not None else pd.DataFrame()

        try:
            return await _run_query(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"tushare get_market_fund_flow 失败: {exc}",
                provider="tushare", method="get_market_fund_flow", cause=exc,
            ) from exc

    async def get_northbound_flow(self, **kwargs: Any) -> pd.DataFrame:
        """获取北向资金（沪深港通）。"""
        api = _get_api()

        def _fetch() -> pd.DataFrame:
            today = date.today().strftime("%Y%m%d")
            df = api.moneyflow_hsgt(start_date=today, end_date=today)
            return df if df is not None else pd.DataFrame()

        try:
            return await _run_query(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"tushare get_northbound_flow 失败: {exc}",
                provider="tushare", method="get_northbound_flow", cause=exc,
            ) from exc

    async def get_index_daily_bars(
        self,
        symbol: str = "000300.SH",
        start_date: str = "",
        end_date: str = "",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """获取指数日 K 线（沪深300/中证500等）。"""
        api = _get_api()
        sd = start_date.replace("-", "") or "20200101"
        ed = end_date.replace("-", "") or date.today().strftime("%Y%m%d")

        def _fetch() -> pd.DataFrame:
            df = api.index_daily(ts_code=symbol, start_date=sd, end_date=ed)
            if df is None or df.empty:
                return pd.DataFrame()
            df = df.sort_values("trade_date").reset_index(drop=True)
            return df

        try:
            return await _run_query(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"tushare get_index_daily_bars 失败: {exc}",
                provider="tushare", method="get_index_daily_bars", cause=exc,
            ) from exc

    async def get_trade_dates(
        self,
        start_date: str = "20200101",
        end_date: str = "",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """获取交易日历。"""
        api = _get_api()
        sd = start_date.replace("-", "")
        ed = end_date.replace("-", "") if end_date else date.today().strftime("%Y%m%d")

        def _fetch() -> pd.DataFrame:
            df = api.trade_cal(start_date=sd, end_date=ed, is_open="1")
            return df if df is not None else pd.DataFrame()

        try:
            return await _run_query(_fetch)
        except Exception as exc:
            raise ProviderError(
                f"tushare get_trade_dates 失败: {exc}",
                provider="tushare", method="get_trade_dates", cause=exc,
            ) from exc
