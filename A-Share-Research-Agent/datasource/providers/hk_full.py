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

import pandas as pd

from datasource.providers._shared.utils import fmt_date, standardize_kline_df
from datasource.providers.akshare import AkshareProvider

from datasource.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("hk_full")
class HKFullProvider:
    """港股全量 Provider — DataSource 的 'hk_full' provider。

    注：港股代码是 5 位（不带前导 0）；symbol normalize 仅大写化。
    """

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @property
    def name(self) -> str:
        return "hk_full"

    async def health_check(self) -> bool:
        """依赖检查：需要 akshare 可用。"""
        try:
            self._ak._ensure_akshare()
            return True
        except Exception:
            return False

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "hk_full" in provs)

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        s = str(symbol).strip()
        if s.startswith(("HK.", "hk.")):
            s = s.split(".", 1)[1]
        return s.zfill(5).upper()

    async def get_stock_list(self) -> pd.DataFrame:
        """港股全量列表（东财源：含主板/创业板代码）。"""
        try:
            logger.debug("正在拉取港股全量列表")
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
            logger.debug("正在拉取港股日 K: %s", code)
            call_kwargs = {
                "symbol": code,
                "period": "daily",
                "adjust": adjust,
            }
            if start_date:
                call_kwargs["start_date"] = fmt_date(start_date)
            if end_date:
                call_kwargs["end_date"] = fmt_date(end_date)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_hk_hist,
                **call_kwargs,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return standardize_kline_df(df)
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
            logger.debug("正在拉取港股指数: %s", symbol)
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
                    mask = pd.Series(True, index=df.index)
                    if start_date:
                        mask &= s >= pd.to_datetime(start_date)
                    if end_date:
                        mask &= s <= pd.to_datetime(end_date)
                    # 用 numpy bool 数组取值，避免索引对齐 reindex 警告
                    df = df.loc[mask.to_numpy()]
            return standardize_kline_df(df)
        except Exception as e:
            logger.error("拉取港股指数失败: symbol=%s error=%s", symbol, e)
            raise
