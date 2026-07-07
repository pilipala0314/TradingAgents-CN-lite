"""HK Full Provider — 港股全量股票（非南向）数据接入。

数据源：akshare (stock_hk_* 系列)
端点：
- get_stock_list_hk_em    → 港股全量列表（东方财富源）
- get_daily_bars_hk_em    → 港股个股日 K
- get_index_daily_bars    → 港股指数日 K（恒生 / 恒生科技 等）

注：南向资金已由 hk_southbound_provider.py 单独负责；本 provider 专注港股本身。
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from data_pipeline.providers.akshare_provider import AkshareProvider

logger = logging.getLogger(__name__)


def _fmt(d: str | date | None) -> str:
    if d is None:
        return ""
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")


class HKFullProvider:
    """港股全量 Provider — DataRouter 的 'hk_full' provider。

    注：港股代码是 5 位（不带前导 0）；symbol normalize 仅大写化。
    """

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        s = str(symbol).strip()
        if s.startswith(("HK.", "hk.")):
            s = s.split(".", 1)[1]
        return s.zfill(5).upper()

    async def get_stock_list(self) -> pd.DataFrame:
        """港股全量列表（东财源：含主板/创业板代码）。"""
        try:
            logger.info("正在拉取港股全量列表")
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_hk_spot_em
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=["code", "name", "exchange"])
            df = df.copy()
            # 东财港股接口列：序号/代码/名称/最新价/涨跌幅/成交额...
            code_col = "代码" if "代码" in df.columns else df.columns[1]
            name_col = "名称" if "名称" in df.columns else df.columns[2]
            df["code"] = df[code_col].astype(str).str.zfill(5)
            df["name"] = df[name_col].astype(str)
            df["exchange"] = "HK"
            return df[["code", "name", "exchange"]]
        except Exception as e:
            logger.error("拉取港股列表失败: %s", e)
            raise

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """港股个股日 K（5 位代码，如 00700 腾讯）。

        Args:
            symbol:    5 位代码
            start_date/end_date: 选填
            adjust:    qfq/hfq/none
        """
        code = self.normalize_symbol(symbol)
        try:
            logger.info("正在拉取港股日 K: %s", code)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_hk_hist,
                symbol=code,
                period="daily",
                start_date=_fmt(start_date),
                end_date=_fmt(end_date),
                adjust=adjust,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return df
        except Exception as e:
            logger.error("拉取港股日 K 失败: symbol=%s error=%s", code, e)
            raise

    async def get_index_daily_bars(
        self,
        symbol: str = "HKHSI",
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> pd.DataFrame:
        """港股指数日 K。

        常用 symbol：
        - HKHSI  恒生指数
        - HKHSTECH  恒生科技
        - HKHSCEI  恒生中国企业指数（H 股）
        """
        try:
            logger.info("正在拉取港股指数: %s", symbol)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_hk_index_daily_em,
                symbol=symbol,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            if start_date or end_date:
                date_col = "date" if "date" in df.columns else "日期"
                if date_col in df.columns:
                    s = pd.to_datetime(df[date_col], errors="coerce")
                    if start_date:
                        df = df[s >= pd.to_datetime(start_date)]
                    if end_date:
                        df = df[s <= pd.to_datetime(end_date)]
            return df
        except Exception as e:
            logger.error("拉取港股指数失败: symbol=%s error=%s", symbol, e)
            raise
