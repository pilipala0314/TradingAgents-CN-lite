"""US Stock Provider — 美股数据接入（仅基础覆盖）。

数据源：akshare (stock_us_* 系列)
端点：
- get_stock_list          → 美股全量列表（东财源）
- get_daily_bars          → 美股个股日 K
- get_spot_quote          → 美股实时报价

说明：
- 美股交易日历与 A 股不同（夏令时 / 冬令时影响开盘时间）；
- 美股行情 akshare 偶尔失效，已加 fallback 上层重试，不在本 provider 范围内处理。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from data_pipeline.providers.akshare_provider import AkshareProvider

logger = logging.getLogger(__name__)


def _fmt(d: str | date | None) -> str:
    if d is None:
        return ""
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")


class USStockProvider:
    """美股 Provider — DataRouter 的 'us_stock' provider。

    注：美股代码常见 1-5 位字母代码（AAPL, TSLA, MSFT 等），不做归一补零。
    """

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        s = str(symbol).strip()
        if s.startswith(("US.", "us.")):
            s = s.split(".", 1)[1]
        return s.upper()

    async def get_stock_list(self) -> pd.DataFrame:
        """美股全量列表（东财源）。"""
        try:
            logger.info("正在拉取美股全量列表")
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_us_spot_em
            )
            if df is None or df.empty:
                return pd.DataFrame(columns=["code", "name", "exchange"])
            df = df.copy()
            code_col = "代码" if "代码" in df.columns else df.columns[1]
            name_col = "名称" if "名称" in df.columns else df.columns[2]
            df["code"] = df[code_col].astype(str).str.upper()
            df["name"] = df[name_col].astype(str)
            df["exchange"] = "US"
            return df[["code", "name", "exchange"]]
        except Exception as e:
            logger.error("拉取美股列表失败: %s", e)
            raise

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        """美股个股日 K（如 AAPL, TSLA）。

        Args:
            symbol:    股票代码（字母，如 AAPL）
            start_date/end_date: 选填
            adjust:    qfq/hfq/none
        """
        code = self.normalize_symbol(symbol)
        try:
            logger.info("正在拉取美股日 K: %s", code)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_us_hist,
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
            logger.error("拉取美股日 K 失败: symbol=%s error=%s", code, e)
            raise

    async def get_spot_quote(self, symbol: str) -> dict[str, Any]:
        """美股实时报价。"""
        code = self.normalize_symbol(symbol)
        try:
            logger.info("正在拉取美股实时报价: %s", code)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_us_spot_em
            )
            if df is None or df.empty:
                return {"symbol": code, "price": 0.0, "chg_pct": 0.0}
            row = df[df["代码"].astype(str).str.upper() == code] if "代码" in df.columns else df.iloc[0:0]
            if row.empty:
                return {"symbol": code, "price": 0.0, "chg_pct": 0.0}
            r = row.iloc[0]
            return {
                "symbol": code,
                "name": str(r.get("名称", "")),
                "price": float(r.get("最新价", 0) or 0),
                "chg_pct": float(r.get("涨跌幅", 0) or 0),
            }
        except Exception as e:
            logger.error("拉取美股实时报价失败: symbol=%s error=%s", code, e)
            raise
