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
from typing import Any

import pandas as pd

from datasource.errors import ProviderError
from datasource.providers._http import get_shared_client
from datasource.providers._shared.utils import fmt_date, standardize_kline_df
from datasource.providers.akshare import AkshareProvider

from datasource.registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("us_stock")
class USStockProvider:
    """美股 Provider — DataSource 的 'us_stock' provider。

    注：美股代码常见 1-5 位字母代码（AAPL, TSLA, MSFT 等），不做归一补零。
    """

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    @property
    def name(self) -> str:
        return "us_stock"

    async def health_check(self) -> bool:
        """依赖检查：需要 akshare 可用（yahoo 兜底走标准 HTTP）。"""
        try:
            self._ak._ensure_akshare()
            return True
        except Exception:
            return False

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "us_stock" in provs)

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        s = str(symbol).strip()
        if s.startswith(("US.", "us.")):
            s = s.split(".", 1)[1]
        return s.upper()

    async def get_stock_list(self) -> pd.DataFrame:
        """美股全量列表（东财源）。失败直接抛（不返回硬编码假列表）。"""
        try:
            logger.debug("正在拉取美股全量列表")
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
        start_date: str | None = None,
        end_date: str | None = None,
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
            logger.debug("正在拉取美股日 K: %s", code)
            try:
                df = await self._ak._run_with_retry(
                    self._ak._ensure_akshare().stock_us_hist,
                    symbol=code,
                    period="daily",
                    start_date=fmt_date(start_date),
                    end_date=fmt_date(end_date),
                    adjust=adjust,
                )
            except Exception as exc:
                logger.warning("美股日 K akshare 失败，转 yahoo 兜底: symbol=%s err=%s", code, exc)
                df = await self._fallback_daily_bars(code)
            if df is None or df.empty:
                return pd.DataFrame()
            return standardize_kline_df(df)
        except Exception as e:
            logger.error("拉取美股日 K 失败: symbol=%s error=%s", code, e)
            raise

    async def get_spot_quote(self, symbol: str) -> dict[str, Any]:
        """美股实时报价。

        数据获取失败时抛 ProviderError（不返回 0.0 假数据）。
        """
        code = self.normalize_symbol(symbol)
        try:
            logger.debug("正在拉取美股实时报价: %s", code)
            try:
                df = await self._ak._run_with_retry(
                    self._ak._ensure_akshare().stock_us_spot_em
                )
            except Exception as exc:
                logger.warning("美股报价 akshare 失败，转 yahoo 兜底: symbol=%s err=%s", code, exc)
                return await self._fallback_spot_quote(code)
            if df is None or df.empty:
                raise ProviderError(
                    f"美股实时报价无数据: {code}", provider="us_stock", method="get_spot_quote",
                )
            row = df[df["代码"].astype(str).str.upper() == code] if "代码" in df.columns else df.iloc[0:0]
            if row.empty:
                raise ProviderError(
                    f"美股 {code} 不在全量列表中", provider="us_stock", method="get_spot_quote",
                )
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

    async def _fallback_daily_bars(self, symbol: str) -> pd.DataFrame:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1mo"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        client = await get_shared_client()
        resp = await client.get(url, headers=headers, timeout=10)
        if resp.status_code == 429:
            raise RuntimeError("Yahoo chart 接口限流(429)")
        resp.raise_for_status()
        payload = resp.json()
        result = (((payload or {}).get("chart") or {}).get("result") or [None])[0] or {}
        timestamps = result.get("timestamp") or []
        quote = (((result.get("indicators") or {}).get("quote")) or [None])[0] or {}
        rows = []
        for idx, ts in enumerate(timestamps):
            rows.append(
                {
                    "date": pd.to_datetime(ts, unit="s").strftime("%Y-%m-%d"),
                    "open": (quote.get("open") or [None])[idx],
                    "high": (quote.get("high") or [None])[idx],
                    "low": (quote.get("low") or [None])[idx],
                    "close": (quote.get("close") or [None])[idx],
                    "volume": (quote.get("volume") or [None])[idx],
                }
            )
        if not rows:
            raise ProviderError(
                f"Yahoo chart 无可用 K 线数据: {symbol}", provider="us_stock", method="get_daily_bars",
            )
        return standardize_kline_df(pd.DataFrame(rows).dropna(subset=["date"]))

    async def _fallback_spot_quote(self, symbol: str) -> dict[str, Any]:
        df = await self._fallback_daily_bars(symbol)
        if df.empty:
            raise ProviderError(
                f"Yahoo 无可用报价数据: {symbol}", provider="us_stock", method="get_spot_quote",
            )
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        prev_close = float(prev.get("close") or 0)
        price = float(last.get("close") or 0)
        chg_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0.0
        return {"symbol": symbol, "name": symbol, "price": price, "chg_pct": chg_pct}
