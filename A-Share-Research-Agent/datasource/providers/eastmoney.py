"""EastMoney Push API Provider — 东方财富直连 HTTP 接口。

特点：
- 直连东财 Push2 API（非爬虫，比 akshare 稳定 10 倍）
- 实时行情：毫秒级延迟
- 资金流向：主力/超大单/大单/中单/小单
- 板块数据：行业/概念板块涨跌排名
- 无需 Token，免费使用
- 有请求频率限制（建议 ≤5 rps）

适用场景：
- 实时行情获取（替代 akshare 的 stock_zh_a_spot_em）
- 资金流向数据（替代 akshare 的 stock_individual_fund_flow）
- 板块涨跌排名（替代 akshare 的 stock_board_industry_name_em）

API 文档：
- 行情：https://push2.eastmoney.com/api/qt/stock/get
- 资金流：https://push2.eastmoney.com/api/qt/stock/fflow/kline/get
- 板块：https://push2.eastmoney.com/api/qt/clist/get
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pandas as pd

from datasource.errors import ProviderError
from datasource.providers._http import http_get_json, safe_float
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

# 东财 API 基础 URL
_PUSH2_BASE = "https://push2.eastmoney.com/api/qt"
_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def _market_prefix(symbol: str) -> tuple[int, str]:
    """返回 (secid 前缀, 纯代码)。secid: 1=沪 0=深。"""
    symbol = symbol.strip().split(".")[0]
    if symbol.startswith(("6", "9")):
        return 1, symbol
    elif symbol.startswith(("0", "2", "3")):
        return 0, symbol
    elif symbol.startswith(("4", "8")):
        return 0, symbol
    return 1, symbol


async def _em_get(url: str, params: dict) -> dict:
    """发送 GET 请求到东财 API（复用全局连接池）。"""
    return await http_get_json(url, params=params, headers=_EM_HEADERS, timeout=10)


@register_provider("eastmoney")
class EastMoneyProvider:
    """东方财富 Push2 API Provider — 实时行情 + 资金流。"""

    @property
    def name(self) -> str:
        return "eastmoney"

    async def health_check(self) -> bool:
        """轻量检查：请求一个简单接口验证网络可达。"""
        try:
            data = await _em_get(
                f"{_PUSH2_BASE}/stock/get",
                {"secid": "1.000001", "fields": "f43", "ut": "fa5fd1943c7b386f172d6893dbbd1"},
            )
            return data.get("rc", 1) == 0
        except Exception:
            return False

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "eastmoney" in provs)

    async def get_tencent_quote(self, symbols: list[str] | str, **kwargs: Any) -> dict:
        """获取实时行情（支持批量，并发拉取）。

        Args:
            symbols: 单个代码或代码列表

        Returns:
            {symbol: {name, current, open, high, low, prev_close, volume, amount, pct_change, ...}}
            （current 为最新价，与 sina/netease/a_stock_data 的 get_tencent_quote 契约对齐）
        """
        if isinstance(symbols, str):
            symbols = [symbols]

        if len(symbols) > 20:  # 限制单次最多 20 个，超出部分明确告警避免静默丢数据
            logger.warning("get_tencent_quote 批量超限: 传入 %d 个，仅处理前 20 个", len(symbols))
            symbols = symbols[:20]

        async def _fetch_one(symbol: str) -> tuple[str, dict | None]:
            market, code = _market_prefix(symbol)
            try:
                data = await _em_get(
                    f"{_PUSH2_BASE}/stock/get",
                    {
                        "secid": f"{market}.{code}",
                        "fields": "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,f116,f117,f162,f167,f170",
                        "ut": "fa5fd1943c7b386f172d6893dbbd1",
                    },
                )
                d = data.get("data") or {}
                if d:
                    current = safe_float(d.get("f43")) / 100 if d.get("f43") else None
                    return code, {
                        "name": d.get("f58", ""),
                        "current": current,
                        "close": current,  # 兼容别名：最新价
                        "open": safe_float(d.get("f46")) / 100 if d.get("f46") else None,
                        "high": safe_float(d.get("f44")) / 100 if d.get("f44") else None,
                        "low": safe_float(d.get("f45")) / 100 if d.get("f45") else None,
                        "prev_close": safe_float(d.get("f60")) / 100 if d.get("f60") else None,
                        "volume": safe_float(d.get("f47")),
                        "amount": safe_float(d.get("f48")),
                        "pct_change": safe_float(d.get("f170")) / 100 if d.get("f170") else None,
                        "pe": safe_float(d.get("f162")) / 100 if d.get("f162") else None,
                        "pb": safe_float(d.get("f167")) / 100 if d.get("f167") else None,
                        "total_mv": safe_float(d.get("f116")),
                        "circ_mv": safe_float(d.get("f117")),
                    }
            except Exception as exc:
                logger.warning("eastmoney 行情获取失败 %s: %s", symbol, exc)
            return code, None

        # 并发拉取所有 symbol
        results = await asyncio.gather(*[_fetch_one(s) for s in symbols], return_exceptions=True)
        result = {}
        for item in results:
            if isinstance(item, Exception):
                continue
            code, quote = item
            if quote:
                result[code] = quote

        if not result:
            raise ProviderError(
                "eastmoney 实时行情全部失败",
                provider="eastmoney", method="get_tencent_quote",
            )
        return result

    async def get_individual_fund_flow(
        self, symbol: str, **kwargs: Any,
    ) -> pd.DataFrame:
        """获取个股资金流向（主力/超大单/大单/中单/小单）。"""
        market, code = _market_prefix(symbol)
        try:
            data = await _em_get(
                f"{_PUSH2_BASE}/stock/fflow/kline/get",
                {
                    "secid": f"{market}.{code}",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                    "klt": "101",  # 日级
                    "lmt": "30",   # 最近 30 天
                    "ut": "b2884a393a59ad64002292a3e90d46a5",
                },
            )
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                return pd.DataFrame()
            rows = []
            for line in klines:
                parts = line.split(",")
                if len(parts) >= 7:
                    rows.append({
                        "date": parts[0],
                        "main_net_inflow": safe_float(parts[1]),
                        "small_net_inflow": safe_float(parts[2]),
                        "mid_net_inflow": safe_float(parts[3]),
                        "large_net_inflow": safe_float(parts[4]),
                        "super_large_net_inflow": safe_float(parts[5]),
                    })
            return pd.DataFrame(rows)
        except Exception as exc:
            raise ProviderError(
                f"eastmoney get_individual_fund_flow 失败: {exc}",
                provider="eastmoney", method="get_individual_fund_flow", cause=exc,
            ) from exc

    async def get_industry_ranking(self, limit: int = 50, **kwargs: Any) -> list[dict]:
        """获取行业板块涨跌排名。"""
        try:
            data = await _em_get(
                f"{_PUSH2_BASE}/clist/get",
                {
                    "pn": "1",
                    "pz": str(limit),
                    "po": "1",
                    "np": "1",
                    "fltt": "2",
                    "invt": "2",
                    "fid": "f3",
                    "fs": "m:90+t:2",  # 行业板块
                    "fields": "f2,f3,f4,f8,f12,f14,f104,f105,f128,f136,f140",
                    "ut": "b2884a393a59ad64002292a3e90d46a5",
                },
            )
            items = data.get("data", {}).get("diff", [])
            result = []
            for item in items:
                result.append({
                    # 规范字段名：与 a_stock_data / sina 板块接口对齐（注册表按 board_name/change_pct 取值）
                    "board_code": item.get("f12", ""),
                    "board_name": item.get("f14", ""),
                    "change_pct": item.get("f3", 0),
                    "close": item.get("f2", 0),
                    "turnover": item.get("f8", 0),
                    "up_count": item.get("f104", 0),
                    "down_count": item.get("f105", 0),
                    "lead_stock": item.get("f128", ""),      # f128=领涨股名称（f140 是代码，勿写反）
                    "lead_code": item.get("f140", ""),       # f140=领涨股代码
                    "lead_change_pct": item.get("f136", 0),  # f136=领涨股涨跌幅
                })
            return result
        except Exception as exc:
            raise ProviderError(
                f"eastmoney get_industry_ranking 失败: {exc}",
                provider="eastmoney", method="get_industry_ranking", cause=exc,
            ) from exc
