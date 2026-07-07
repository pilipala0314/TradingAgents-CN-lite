"""GEMKlineProvider — 创业板（深圳证券交易所创业板）K 线数据接入。

数据源：akshare (stock_zh_a_spot_em, stock_zh_a_hist)
- get_stock_list: 复用 stock_zh_a_spot_em，筛 300/301 开头代码
- get_daily_bars: stock_zh_a_hist(symbol="300xxx", period="daily", adjust=...)

创业板代码特征：6 位数字，以 300/301 开头。
"""
from __future__ import annotations

import logging
import re
from datetime import date

import pandas as pd

from data_pipeline.providers.akshare_provider import AkshareProvider

logger = logging.getLogger(__name__)

# 创业板代码正则：300/301 开头 6 位数字
_GEM_CODE_RE = re.compile(r"^30[01]\d{3}$")


def _fmt(d: str | date | None) -> str:
    if d is None:
        return ""
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")


class GEMKlineProvider:
    """创业板 K 线 Provider — DataRouter 的 'gem' provider。

    注：与 A 股主板 (stock_zh_a_hist) 共用接口；本类仅做代码过滤（300/301 开头）
    和创业板语义标注，不绕过 akshare。
    """

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """归一化 6 位代码。BJ./SZ. 前缀去除（创业板属 SZ）。"""
        s = str(symbol).strip()
        for prefix in ("BJ.", "bj.", "BSE.", "bse.", "SZ.", "sz.", "SH.", "sh."):
            if s.startswith(prefix):
                s = s.split(".", 1)[1]
                break
        s = s.upper()
        if s.isdigit():
            s = s.zfill(6)
        return s

    @staticmethod
    def is_gem_symbol(symbol: str) -> bool:
        """判定一个 symbol 是否属于创业板板块。"""
        s = GEMKlineProvider.normalize_symbol(symbol)
        return bool(_GEM_CODE_RE.match(s))

    async def get_stock_list(self) -> pd.DataFrame:
        """拉全 A 股列表 → 筛 300/301 开头。

        Returns:
            DataFrame(columns=["code", "name", "exchange"]), exchange="SZ"
        """
        try:
            logger.info("正在拉取 A 股全量列表（用于过滤创业板）")
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_zh_a_spot_em
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=["code", "name", "exchange"])
            df = df.copy()
            # 列名兼容：东财常用"代码/名称"
            code_col = "代码" if "代码" in df.columns else df.columns[1]
            name_col = "名称" if "名称" in df.columns else df.columns[2]
            df["code"] = df[code_col].astype(str).str.zfill(6)
            df["name"] = df[name_col].astype(str)
            df = df[df["code"].apply(self.is_gem_symbol)]
            df["exchange"] = "SZ"
            return df[["code", "name", "exchange"]]
        except Exception as e:
            logger.error("拉取创业板列表失败: %s", e)
            raise

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """创业板个股日 K（6 位代码，如 300750 宁德时代）。

        Args:
            symbol:    6 位代码
            start_date/end_date: 选填
            adjust:    qfq/hfq/none
        """
        code = self.normalize_symbol(symbol)
        if not self.is_gem_symbol(code):
            raise ValueError(
                f"GEMKlineProvider 不支持非创业板代码: {code}（需 300/301 开头）"
            )
        try:
            logger.info("正在拉取创业板日 K: %s", code)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_zh_a_hist,
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
            logger.error("拉取创业板日 K 失败: symbol=%s error=%s", code, e)
            raise
