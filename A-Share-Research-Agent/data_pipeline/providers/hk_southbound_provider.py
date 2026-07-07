"""HKSouthboundProvider — 港股通南向资金流 Provider。

数据源：东方财富 push2.eastmoney.com `kamt` 接口（南向资金，含沪/深港通）。

端点：
- `get_southbound_flow`  → 南向资金流快照（沪 + 深）
- `get_top_stocks`       → 南向资金 top 领涨个股（按涨跌幅）

返回值统一为 `list[dict]` / `dict`（与 facade / agent 层契约一致）。

失败语义：永不静默降级；失败直接 raise（DataRouter 决定 fallback）。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# 东方财富 push2 南向资金接口
# 沪港通南向 market=001；深港通南向 market=002
_KAMT_URL = (
    "https://push2.eastmoney.com/api/qt/kamt/get"
    "?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56&klt=1&lmt=1&fields=f51,f52,f54,f55&market={market}"
)

# 领涨个股详情（深/沪港通南向）
_KAMT_TOP_URL = (
    "https://push2.eastmoney.com/api/qt/kamt/get"
    "?fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56&klt=1&lmt=5&fields=f12,f14,f3,f2&market={market}"
)

# market -> 名称映射
_MARKET_NAME = {
    "001": "沪港通南向",
    "002": "深港通南向",
}


class HKSouthboundProvider:
    """港股通南向资金 Provider — DataRouter 的 'hk_southbound' provider。"""

    def __init__(self, timeout: float = 8.0, transport: httpx.BaseTransport | None = None) -> None:
        self.timeout = timeout
        self._transport = transport
        self._headers = {
            "Referer": "https://quote.eastmoney.com/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        """统一 GET 入口（业务方应通过 ResiliencePolicy 加重试 / 限流）。"""
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._headers, transport=self._transport) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def get_southbound_flow(self) -> dict[str, Any]:
        """南向资金流快照（沪 + 深两路分别取，再合并）。

        Returns:
            {
                "sh": {"market": "沪港通南向", "net_buy": float, "amount": float, "top_stock": str, "top_chg_pct": float, "ts": int},
                "sz": {"market": "深港通南向", "net_buy": float, "amount": float, "top_stock": str, "top_chg_pct": float, "ts": int},
                "ts": int,  # 最近一次成功响应的时间戳（秒）
            }

        push2.eastmoney.com kamt 接口字段含义（实测惯例）：
        - f51: 成交额
        - f52: 净买额
        - f53: 领涨股代码
        - f54: 领涨股名称
        - f55: 领涨股涨跌幅（%）
        - f56: 领涨股最新价
        """
        out: dict[str, Any] = {}
        for market_key in ("001", "002"):
            url = _KAMT_URL.format(market=market_key)
            data = await self._fetch_json(url)
            if not data or "data" not in data or not data["data"]:
                # 静默记录，不抛（合并需要两路都成功才抛，单路失败降级）
                logger.warning("南向资金接口无数据: market=%s resp=%s", market_key, data)
                continue
            d = data["data"]
            sub = {
                "market": _MARKET_NAME[market_key],
                "net_buy": float(d.get("f52", 0) or 0),  # 净买额（元）
                "amount": float(d.get("f51", 0) or 0),  # 成交额（元）
                "top_stock": str(d.get("f54", "") or ""),  # 领涨股名称
                "top_stock_code": str(d.get("f53", "") or ""),  # 领涨股代码
                "top_chg_pct": float(d.get("f55", 0) or 0),  # 领涨股涨跌幅（%）
                "ts": int(data.get("dt", 0) or 0),  # 本市场数据时间戳（秒，字符串转 int）
            }
            out["sh" if market_key == "001" else "sz"] = sub
        if not out:
            raise ValueError("港股通南向资金接口返回空")
        # 取最近一次成功响应的时间戳作为合并 ts（避免循环外 data 引用歧义）
        out["ts"] = max(
            (int(v.get("ts", 0) or 0) for v in (out.get("sh"), out.get("sz")) if v),
            default=0,
        )
        return out

    async def get_top_stocks(self, market: str = "001", topn: int = 5) -> list[dict[str, Any]]:
        """南向资金 top 领涨个股。

        Args:
            market: "001" 沪港通南向 / "002" 深港通南向
            topn:   返回 top N（默认 5）

        Returns:
            list[dict]，每条含：code / name / price / chg_pct
        """
        url = _KAMT_TOP_URL.format(market=market)
        data = await self._fetch_json(url)
        rows = (data or {}).get("data") or []
        out: list[dict[str, Any]] = []
        for r in rows[:topn]:
            out.append({
                "code": str(r.get("f12", "") or ""),
                "name": str(r.get("f14", "") or ""),
                "price": float(r.get("f2", 0) or 0),
                "chg_pct": float(r.get("f3", 0) or 0),
                "market": _MARKET_NAME.get(market, market),
            })
        return out
