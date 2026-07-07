"""BSE Provider — 北京证券交易所 (Beijing Stock Exchange) 数据接入。

数据源：akshare (stock_bse_* 系列)
端点：
- get_stock_list        → 北交所全量股票
- get_stock_daily_bars  → 个股日 K
- get_index_daily_bars  → 北证 50 指数日 K

北交所股票代码段：8 开头（如 830xxx, 831xxx, 836xxx, 837xxx 等）
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from data_pipeline.providers.akshare_provider import AkshareProvider

logger = logging.getLogger(__name__)


class BSEProvider:
    """北交所 Provider — DataRouter 的 'bse' provider。

    不直接调用 akshare API；委托 AkshareProvider 的 _run_with_retry 保证韧性一致。
    """

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """北交所代码统一 6 位字符串。"""
        s = str(symbol).strip()
        if s.startswith(("BJ.", "bj.")):
            s = s.split(".", 1)[1]
        return s.zfill(6)

    async def get_stock_list(self) -> pd.DataFrame:
        """北交所全量股票（含 8 开头代码段）。"""
        try:
            logger.info("正在拉取北交所股票列表")
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_bse_spot_em
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=["code", "name", "exchange"])
            df = df.copy()
            df["code"] = df["代码"].astype(str).str.zfill(6)
            df["name"] = df["名称"].astype(str)
            df["exchange"] = "BSE"
            return df[["code", "name", "exchange"]]
        except Exception as e:
            logger.error("拉取北交所股票列表失败: %s", e)
            raise

    async def get_stock_daily_bars(
        self,
        symbol: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> pd.DataFrame:
        """北交所个股日 K。

        Args:
            symbol:    6 位代码（如 830xxx）
            start_date/end_date: 选填，YYYYMMDD / YYYY-MM-DD
        """
        code = self.normalize_symbol(symbol)
        try:
            logger.info("正在拉取北交所日 K: %s", code)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_zh_b_daily,
                symbol=code,
                start_date=_fmt(start_date),
                end_date=_fmt(end_date),
                adjust="qfq",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return df.rename(columns={
                "date": "date", "open": "open", "close": "close",
                "high": "high", "low": "low", "volume": "volume",
            })
        except Exception as e:
            logger.error("拉取北交所日 K 失败: symbol=%s error=%s", code, e)
            raise


def _fmt(d: str | date | None) -> str:
    if d is None:
        return ""
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")


class BSEIndexProvider:
    """北证 50 指数 Provider。"""

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    async def get_index_daily_bars(
        self,
        symbol: str = "BJ899050",
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> pd.DataFrame:
        """北证指数日 K（默认 899050 北证 50）。"""
        try:
            logger.info("正在拉取北证指数: %s", symbol)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_zh_index_daily,
                symbol=symbol,
                start_date=_fmt(start_date),
                end_date=_fmt(end_date),
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return df
        except Exception as e:
            logger.error("拉取北证指数失败: symbol=%s error=%s", symbol, e)
            raise
