"""SWIndustryProvider — 申万一级行业指数 Provider（P0-T4）。

底层数据源：akshare
- `ak.stock_board_industry_name_em()` → 申万一级 31 个行业指数（板块名称 + 板块代码）
- `ak.stock_board_industry_hist_em(symbol, period, start_date, end_date)` → K 线

DataRouter 端点：
- `get_sw_industry_list()` → [{code, name}, ...]（31 个）
- `get_sw_industry_bars(symbol, start_date, end_date)` → DataFrame

失败语义：网络失败 → 抛出（不静默），由调用方（ETL）处理。
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _fetch_sw_industry_list() -> pd.DataFrame:
    """同步拉一次申万一级 31 个行业指数列表。"""
    import akshare as ak
    df = ak.stock_board_industry_name_em()
    return df


def _fetch_sw_industry_bars(
    symbol: str,
    period: str = "日k",
    start_date: str | None = None,
    end_date: str | None = None,
    adjust: str = "hfq",
) -> pd.DataFrame:
    """同步拉一次申万行业指数 K 线。"""
    import akshare as ak
    df = ak.stock_board_industry_hist_em(
        symbol=str(symbol),
        period=period,
        start_date=start_date or "20200101",
        end_date=end_date or "20991231",
        adjust=adjust,
    )
    return df


class SWIndustryProvider:
    """申万一级行业指数 Provider — DataRouter 的 'sw_industry' provider。"""

    def __init__(self) -> None:
        pass

    async def get_index_list(self) -> pd.DataFrame:
        """返回申万一级 31 个行业指数列表（[code, name]）。

        Returns:
            DataFrame，列：code（6 位字符串）, name（中文行业名）
        """
        import asyncio
        df = await asyncio.to_thread(_fetch_sw_industry_list)
        if df.empty:
            return df
        # akshare 列名规范化为 code / name
        rename: dict[str, str] = {}
        for col in df.columns:
            if "代码" in col or "板块代码" in col:
                rename[col] = "code"
            elif "名称" in col or "板块名称" in col:
                rename[col] = "name"
        df = df.rename(columns=rename)
        if "code" not in df.columns or "name" not in df.columns:
            logger.warning(
                "ak.stock_board_industry_name_em 返回列异常, columns=%s",
                list(df.columns),
            )
            return pd.DataFrame(columns=["code", "name"])
        df["code"] = df["code"].astype(str).str.zfill(6)
        return df[["code", "name"]].drop_duplicates(subset=["code"], keep="last")

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "hfq",
    ) -> pd.DataFrame:
        """拉申万行业指数 K 线。

        Args:
            symbol: 6 位行业代码（如 "801010" 农林牧渔）
            start_date/end_date: YYYYMMDD
            adjust: hfq / qfq / none
        """
        import asyncio
        df = await asyncio.to_thread(
            _fetch_sw_industry_bars,
            symbol, "日k", start_date, end_date, adjust,
        )
        return df
