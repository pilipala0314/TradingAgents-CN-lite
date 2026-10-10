"""行情层：mootdx K 线 / 实时报价 + 腾讯 PE/PB 估值。

数据源优先级（按 a-stock-data SKILL 原则）：
- 1) mootdx：K 线、五档盘口、逐笔成交、财务快照（TCP 7709，不封 IP）。
- 2) 腾讯：PE/PB/市值/换手/涨跌停（HTTP GBK，不封 IP）。
"""

from __future__ import annotations

import logging
from typing import Any

from datasource.providers._shared import normalize_ticker, tdx_query, tencent_quote
from datasource.providers._shared.utils import to_native

logger = logging.getLogger(__name__)


# mootdx 频率：0=5分钟 1=15分钟 2=30分钟 3=60分钟 4=日线 5=周线 6=月线
# 8=1分钟 9=日线(默认) 10=季线 11=年线
FREQ_DAILY = 9
FREQ_5MIN = 0
FREQ_1MIN = 8


def fetch_kline(
    code: str,
    frequency: int = FREQ_DAILY,
    offset: int = 60,
) -> list[dict[str, Any]]:
    """拉取 K 线（不复权；跨除权日需自行复权或改用腾讯）。

    frequency: 见 FREQ_* 常量。
    offset: 拉取根数（默认 60 根日线 ≈ 一季度）。
    返回: [{date, open, close, high, low, volume, amount}, ...]
    （列名与全库 K 线契约对齐，vol → volume）
    """
    code = normalize_ticker(code)
    df = tdx_query(lambda c: c.bars(symbol=code, frequency=frequency, offset=offset))
    if df is None or len(df) == 0:
        return []
    rows: list[dict[str, Any]] = []
    # mootdx DataFrame 列名：datetime / open / close / high / low / vol / amount
    for _, row in df.iterrows():
        rows.append(
            {
                "date": str(row.get("datetime", "")),
                "open": float(row.get("open", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "volume": float(row.get("vol", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
            }
        )
    return rows


def fetch_quote_5level(code: str) -> dict[str, Any]:
    """实时五档盘口 + 46 字段报价（mootdx quotes）。

    失败语义：
    - 网络/连接错误 → raise（DataSource 决定 fallback 到 sina/netease）
    - 协议成功但无报价数据 → raise（"无数据" 也是业务错误，不静默降级）

    返回: {price, open, high, low, last_close, vol, amount, bid1..ask5, ...}
    """
    code = normalize_ticker(code)
    try:
        df = tdx_query(lambda c: c.quotes(symbol=[code]))
    except Exception as exc:
        logger.error("mootdx quotes(%s) 失败: %s", code, exc)
        raise
    if df is None or len(df) == 0:
        raise ValueError(f"mootdx 报价为空: {code}（协议成功但无数据）")
    row = df.iloc[0].to_dict()
    return {k: to_native(v) for k, v in row.items()}


def fetch_tencent_valuation(codes: list[str]) -> dict[str, dict]:
    """腾讯实时估值（PE/PB/市值/换手/涨跌停价/振幅/量比）。"""
    return tencent_quote(codes)
