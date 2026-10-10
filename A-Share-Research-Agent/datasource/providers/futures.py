"""Futures Provider — 国内期货主力合约数据接入。

数据源：akshare (futures_main_* 系列)
端点：
- get_main_contracts_sina   → 主力合约列表（新浪源，免登录）
- get_daily_bars_sina       → 主力合约日 K（新浪源，免登录）
- get_sector_spot           → 期货板块实时快照

国内主力合约：CU/AU/AG/RU/A/M/Y/P/C/CS 等（按交易所）
"""
from __future__ import annotations

import logging

import pandas as pd

from datasource.errors import ProviderError
from datasource.providers.akshare import AkshareProvider

from datasource.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("futures")
class FuturesProvider:
    """期货 Provider — DataSource 的 'futures' provider。"""

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @property
    def name(self) -> str:
        return "futures"

    async def health_check(self) -> bool:
        """依赖检查：需要 akshare 可用。"""
        try:
            self._ak._ensure_akshare()
            return True
        except Exception:
            return False

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "futures" in provs)

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """期货合约代码大写。"""
        return str(symbol).strip().upper()

    async def get_main_contracts_sina(self) -> pd.DataFrame:
        """新浪主力合约列表。

        Returns:
            DataFrame 含 symbol（主力合约代码） / name（品种名） 字段。
        """
        try:
            logger.debug("正在拉取新浪主力合约列表")
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().futures_main_sina
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=["symbol", "name", "exchange"])
            df = df.copy()
            # 新浪主力合约接口通常列：symbol / name
            sym_col = "symbol" if "symbol" in df.columns else (
                "代码" if "代码" in df.columns else (df.columns[0] if len(df.columns) > 0 else None)
            )
            name_col = "name" if "name" in df.columns else (
                "名称" if "名称" in df.columns else (df.columns[1] if len(df.columns) > 1 else df.columns[0])
            )
            df["symbol"] = df[sym_col].astype(str).str.upper()
            df["name"] = df[name_col].astype(str)
            df["exchange"] = "ALL"
            return df[["symbol", "name", "exchange"]]
        except Exception as e:
            logger.error("拉取主力合约失败: %s", e)
            raise ProviderError(
                f"futures get_main_contracts_sina 失败: {e}",
                provider="futures", method="get_main_contracts_sina", cause=e,
            ) from e

    async def get_daily_bars_sina(
        self,
        symbol: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> pd.DataFrame:
        """新浪主力合约日 K（合约代码如 RB2501 / CU2501）。

        注：新浪只返主力合约最近 N 根 K 线（非严格时间区间）。
        """
        code = self.normalize_symbol(symbol)
        try:
            logger.debug("正在拉取期货日 K: %s", code)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().futures_zh_daily_sina,
                symbol=code,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            # 过滤日期（如提供）
            if start_date or end_date:
                df = _filter_by_date(df, start_date, end_date)
            return df
        except Exception as e:
            logger.error("拉取期货日 K 失败: symbol=%s error=%s", code, e)
            raise ProviderError(
                f"futures get_daily_bars_sina 失败: {e}",
                provider="futures", method="get_daily_bars_sina", cause=e,
            ) from e

    async def get_sector_spot(self) -> pd.DataFrame:
        """期货板块实时快照。"""
        try:
            logger.debug("正在拉取期货板块快照")
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().futures_spot_em
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return df
        except Exception as e:
            logger.error("拉取期货板块快照失败: %s", e)
            raise ProviderError(
                f"futures get_sector_spot 失败: {e}",
                provider="futures", method="get_sector_spot", cause=e,
            ) from e


def _filter_by_date(
    df: pd.DataFrame,
    start: str | date | None,
    end: str | date | None,
) -> pd.DataFrame:
    """按日期列过滤。列名兼容性：date / 日期。非法日期输入 → 返回空 DataFrame。"""
    date_col = "date" if "date" in df.columns else ("日期" if "日期" in df.columns else None)
    if date_col is None:
        return df
    s = pd.to_datetime(df[date_col], errors="coerce")
    if start:
        try:
            s_start = pd.to_datetime(start)
        except (ValueError, TypeError):
            return pd.DataFrame()
        df = df[s >= s_start]
        # start 过滤后索引已变化，重新对齐日期列索引，避免 end 过滤触发 reindex
        s = pd.to_datetime(df[date_col], errors="coerce")
    if end:
        try:
            s_end = pd.to_datetime(end)
        except (ValueError, TypeError):
            return pd.DataFrame()
        df = df[s <= s_end]
    return df.reset_index(drop=True)
