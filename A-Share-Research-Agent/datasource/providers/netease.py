"""NeteaseProvider — 网易财经行情 fallback 源。

数据源：`api.money.126.net`（网易免费行情，稳定性好，无明显反爬）。

端点：
- `get_tencent_quote` → 实时行情（list[str] 输入；批量则并发单只拉取后合并）
- `get_daily_bars`     → 日 K 线（CSV 格式，`quotes.money.126.net/service/chddata.html`），返回 pd.DataFrame
- `get_minute_bars`    → 分钟 K 线 — **Netease 公开接口不支持**（抛 ProviderError 标记未实现）

实时行情返回 `dict`；K 线统一返回 `pd.DataFrame`（与其他 provider 对齐）。
"""
from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

import pandas as pd

from datasource.errors import ProviderError
from datasource.providers._http import get_shared_client
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

# 网易实时行情 URL（无后缀；`{symbol}` 是网易代号 0600519/1000636 形式）
_NETEASE_HQ_URL = "https://api.money.126.net/data/feed/{symbol}"
# 网易日 K CSV（chddata 端点）
_NETEASE_KLINE_URL = "https://quotes.money.126.net/service/chddata.html"


def _to_netease_symbol(symbol: str) -> str:
    """网易代号：6→0, 0/3→1, 5→2（基金）。"""
    s = symbol.lower().replace(".sh", "").replace(".sz", "").replace(".bj", "")
    # 去除已有的 sh/sz/bj 前缀
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    if s.startswith("6"):
        return f"0{s}"
    if s.startswith(("0", "3")):
        return f"1{s}"
    if s.startswith(("4", "8")):
        # 北交所，网易代号待确认；暂用 0 前缀
        return f"0{s}"
    return f"1{s}"


@register_provider("netease")
class NeteaseProvider:
    """网易财经 Provider — DataSource 的 'netease' provider。

    Args:
        timeout: HTTP 超时（秒）
    """

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        }

    @property
    def name(self) -> str:
        return "netease"

    async def health_check(self) -> bool:
        """依赖检查：netease 走标准 HTTP，无额外依赖。"""
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "netease" in provs)

    async def _fetch(self, url: str) -> str:
        """统一 GET 入口（复用全局连接池）。"""
        client = await get_shared_client()
        resp = await client.get(url, headers=self._headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.text

    async def get_tencent_quote(self, symbols: list[str] | str) -> dict[str, dict[str, Any]]:
        """实时行情（与 a_stock_data / sina 契约一致，兼容 str 与 list[str]）。

        网易接口一次只能查一只，所以内部并发拉取后合并为
        `dict[原始_symbol -> {name, open, ...}]`。

        URL 形式：
        https://api.money.126.net/data/feed/0600519,1000636
        响应：`_ntes_quote_callback({"0600519":{...},"1000636":{...}});`
        """
        if isinstance(symbols, str):
            symbols = [symbols]
        if not symbols:
            return {}
        return await self._get_tencent_quote_batch(list(symbols))

    async def _get_tencent_quote_batch(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """批量拉取：把 symbols 用逗号拼成一次 URL（网易实际支持多 symbol）。"""
        # 网易实际接受多 symbol 逗号分隔，一次请求返回所有
        netease_codes = [_to_netease_symbol(s) for s in symbols]
        url = _NETEASE_HQ_URL.format(symbol=",".join(netease_codes))
        text = await self._fetch(url)
        # 提取 JSON 主体
        start = text.find("(")
        end = text.rfind(")")
        if start < 0 or end <= start:
            raise ProviderError(
                f"Netease 行情响应非 JSONP 格式: {text[:120]}",
                provider="netease", method="get_tencent_quote",
            )
        try:
            payload = json.loads(text[start + 1:end])
        except json.JSONDecodeError as exc:
            raise ProviderError(
                f"Netease 行情 JSON 解析失败: {exc}",
                provider="netease", method="get_tencent_quote", cause=exc,
            ) from exc
        # 按原始 symbol 索引返回（保持 caller 期望的 key）
        results: dict[str, dict[str, Any]] = {}
        for original_sym, netease_code in zip(symbols, netease_codes, strict=False):
            d = payload.get(netease_code)
            if not d:
                logger.debug("Netease 批量中 %s (=%s) 无数据", original_sym, netease_code)
                continue
            results[original_sym] = self._parse_quote_dict(d)
        if not results:
            sym_str = ",".join(symbols)
            raise ProviderError(
                f"Netease 行情响应无 {sym_str} 数据",
                provider="netease", method="get_tencent_quote",
            )
        return results

    def _parse_quote_dict(self, d: dict[str, Any]) -> dict[str, Any]:
        """把 Netease 单只 dict 解析成标准结构。"""
        return {
            "name": d.get("name", ""),
            "open": float(d.get("open", 0) or 0),
            "prev_close": float(d.get("yestclose", 0) or 0),
            "current": float(d.get("price", 0) or 0),
            "high": float(d.get("high", 0) or 0),
            "low": float(d.get("low", 0) or 0),
            "volume": int(d.get("volume", 0) or 0),
            "amount": float(d.get("turnover", 0) or 0),
            "datetime": str(d.get("time", "")),  # 实际是 "YYYY-MM-DD HH:MM:SS" 完整时间戳
        }

    async def get_daily_bars(
        self,
        symbol: str,
        start_date: str | None = None,
        end_date: str | None = None,
        adjust: str = "",
        **kwargs: Any,
    ) -> pd.DataFrame:
        """日 K 线（CSV 格式，逗号分隔）。

        Args:
            symbol:     6 位代码
            start_date: 起始日期 YYYYMMDD（可选）；兼容旧参数名 start（经 kwargs 传入）
            end_date:   结束日期 YYYYMMDD（可选）；兼容旧参数名 end（经 kwargs 传入）
            adjust:     "" / "qfq" 前复权 / "hfq" 后复权（网易 CSV 源仅支持不复权，保留参数为契约对齐）
        """
        start_date = start_date or kwargs.get("start")
        end_date = end_date or kwargs.get("end")
        params = {
            "code": _to_netease_symbol(symbol),
            "start": start_date or "",
            "end": end_date or "",
            "fields": "TCLOSE;HIGH;LOW;TOPEN;LCLOSE;CHG;PCHG;TURNOVER;VOTURNOVER;VATURNOVER",
        }
        url = f"{_NETEASE_KLINE_URL}?{urlencode(params)}"
        text = await self._fetch(url)
        # CSV 格式：第一行表头，之后每行一天
        # 列序：日期,股票代码,名称,收盘,最高,最低,开盘,前收,涨跌额,涨跌幅,换手率,成交量,成交金额,...
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            return pd.DataFrame()
        rows: list[dict[str, Any]] = []
        for ln in lines[1:]:
            parts = ln.split(",")
            if len(parts) < 12:
                continue
            try:
                rows.append({
                    "date": parts[0],
                    "open": float(parts[6] or 0),
                    "high": float(parts[4] or 0),
                    "low": float(parts[5] or 0),
                    "close": float(parts[3] or 0),
                    "volume": int(float(parts[11] or 0)),
                })
            except (ValueError, IndexError):
                continue
        return pd.DataFrame(rows)

    async def get_minute_bars(
        self,
        symbol: str,
        period: str = "1m",
        datalen: int = 240,
    ) -> list[dict[str, Any]]:
        """分钟 K 线。

        **Netease 公开接口不支持分钟线**（其分钟线需 msc 参数且仅在客户端可用）。
        本方法抛 `ProviderError`（method="get_minute_bars"）— DataSource 兜底链上
        netease 会被视作 "provider 失败"并自动切到下一个 provider（akshare / sina）。
        保留本方法是为了 method_table 的 signature 一致性。
        """
        raise ProviderError(
            "Netease 公开接口不支持分钟 K 线（未实现）；请走 akshare 或 sina 兜底",
            provider="netease", method="get_minute_bars",
        )
