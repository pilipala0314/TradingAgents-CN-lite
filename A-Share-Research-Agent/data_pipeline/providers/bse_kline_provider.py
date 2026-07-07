"""BSEKlineProvider — 北交所个股 K 线接入。

数据源：akshare (stock_bj_* 系列)
- get_stock_list: 抓全 A 列表筛 8 开头 / 4 开头（北交所代码前缀 8/4）
- get_daily_bars: stock_bj_hist_em(symbol=..., adjust=...)

北交所代码 8 位：以 8 开头（830/831/832/833/835/836/837/838/839/870/871 等），
            或 4 开头（4 开头早期挂牌股）。
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


class BSEKlineProvider:
    """北交所 Provider — DataRouter 的 'bse_kline' provider。

    代码 8 位（部分 4 开头早期股），symbol normalize 保 8 位。
    """

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        s = str(symbol).strip()
        if s.startswith(("BJ.", "bj.", "BSE.", "bse.")):
            s = s.split(".", 1)[1]
        # 北交所 8 位 / 早期 4 开头 — 都按数字 pad
        s = s.upper()
        if s.isdigit():
            s = s.zfill(8)
        return s

    async def get_stock_list(self) -> pd.DataFrame:
        """北交所全量列表：抓 stock_zh_a_spot_em 筛 8/4 开头。

        列：code, name, exchange=BJ
        """
        try:
            logger.info("正在拉取北交所全量列表")
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_zh_a_spot_em
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=["code", "name", "exchange"])
            df = df.copy()
            # 列适配
            code_col = "代码" if "代码" in df.columns else df.columns[1]
            name_col = "名称" if "名称" in df.columns else df.columns[2]
            df["code"] = df[code_col].astype(str).str.zfill(8)
            df["name"] = df[name_col].astype(str)
            # 筛北交所：8 开头 + 4 开头
            bj_mask = df["code"].str.startswith(("8", "4"))
            bj_df = df.loc[bj_mask, ["code", "name"]].copy()
            bj_df["exchange"] = "BJ"
            bj_df = bj_df.drop_duplicates(subset=["code"], keep="last")
            logger.info("北交所全量列表: %d 只", len(bj_df))
            return bj_df.reset_index(drop=True)
        except Exception as exc:
            logger.warning("拉取北交所列表失败: %s", exc)
            return pd.DataFrame(columns=["code", "name", "exchange"])

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """北交所个股日 K。

        Args:
            symbol: 8 位代码
            start_date / end_date: YYYYMMDD
            adjust: qfq/hfq/none

        Returns:
            columns: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
        """
        norm = self.normalize_symbol(symbol)
        try:
            logger.info("拉取北交所 K 线: %s [%s, %s] adjust=%s",
                        norm, start_date, end_date, adjust)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_bj_hist_em,
                symbol=norm,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return df
        except Exception as exc:
            logger.warning("拉取北交所 K 线失败: %s err=%s", norm, exc)
            raise
