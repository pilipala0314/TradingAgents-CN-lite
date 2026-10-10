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

import pandas as pd

from datasource.providers._shared.utils import fmt_date, standardize_kline_df
from datasource.providers.akshare import AkshareProvider

from datasource.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("bse")
class BSEProvider:
    """北交所 Provider — DataSource 的 'bse' provider。

    不直接调用 akshare API；委托 AkshareProvider 的 _run_with_retry 保证韧性一致。
    """

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @property
    def name(self) -> str:
        return "bse"

    async def health_check(self) -> bool:
        """依赖检查：需要 akshare 可用。"""
        try:
            self._ak._ensure_akshare()
            return True
        except Exception:
            return False

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "bse" in provs)

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
            logger.debug("正在拉取北交所股票列表")
            ak = self._ak._ensure_akshare()
            fetcher = getattr(ak, "stock_bse_spot_em", None) or getattr(ak, "stock_bj_zh_a_spot_em", None)
            if fetcher is None:
                raise AttributeError("当前 akshare 版本不支持北交所股票列表接口")
            df = await self._ak._run_with_retry(fetcher)
            if df is None or df.empty:
                return pd.DataFrame(columns=["code", "name", "exchange"])
            df = df.copy()
            code_col = "代码" if "代码" in df.columns else df.columns[0]
            name_col = "名称" if "名称" in df.columns else df.columns[1]
            df["code"] = df[code_col].astype(str).str.zfill(6)
            df["name"] = df[name_col].astype(str)
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
            logger.debug("正在拉取北交所日 K: %s", code)
            ak = self._ak._ensure_akshare()
            fetcher = getattr(ak, "stock_bj_hist_em", None)
            if fetcher is None:
                raise AttributeError("当前 akshare 版本不支持北交所日线接口 stock_bj_hist_em")
            df = await self._ak._run_with_retry(
                fetcher,
                symbol=code,
                start_date=fmt_date(start_date),
                end_date=fmt_date(end_date),
                adjust="qfq",
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return standardize_kline_df(df)
        except Exception as e:
            logger.error("拉取北交所日 K 失败: symbol=%s error=%s", code, e)
            raise

@register_provider("bse_index")
class BSEIndexProvider:
    """北证 50 指数 Provider。"""

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @property
    def name(self) -> str:
        return "bse_index"

    async def health_check(self) -> bool:
        """依赖检查：需要 akshare 可用。"""
        try:
            self._ak._ensure_akshare()
            return True
        except Exception:
            return False

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "bse_index" in provs)

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """北证指数代码 → akshare 小写 bj 前缀格式（bj899050）。"""
        s = str(symbol).strip().lower()
        if s.startswith("bj"):
            s = s[2:]
        return f"bj{s.zfill(6)}"

    async def get_index_daily_bars(
        self,
        symbol: str = "BJ899050",
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> pd.DataFrame:
        """北证指数日 K（默认 899050 北证 50）。"""
        try:
            code = self.normalize_symbol(symbol)
            logger.debug("正在拉取北证指数: %s", code)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_zh_index_daily,
                symbol=code,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            if start_date or end_date:
                # 兼容不同 akshare 版本的日期列名；都没有则跳过过滤
                date_col = next((c for c in ("date", "日期", "trade_date") if c in df.columns), None)
                if date_col is not None:
                    dt = pd.to_datetime(df[date_col], errors="coerce")
                    mask = pd.Series(True, index=df.index)
                    if start_date:
                        mask &= dt >= pd.to_datetime(start_date)
                    if end_date:
                        mask &= dt <= pd.to_datetime(end_date)
                    # 用 numpy bool 数组取值，避免索引对齐 reindex 警告
                    df = df.loc[mask.to_numpy()]
            return standardize_kline_df(df)
        except Exception as e:
            logger.error("拉取北证指数失败: symbol=%s error=%s", symbol, e)
            raise
