"""腾讯财经实时行情（PE/PB/市值/换手/涨跌停）— GBK 编码。

提供 `tencent_quote(codes)` 批量拉取函数（A 股 sh/sz/bj 前缀），零鉴权，HTTP 不封 IP。
国际/全球标的（港股 hk / 美股 us / 指数）走 `GlobalQuoteProvider`，
其内部复用 `_parse_quote_response()` 解析逻辑，避免重复造轮子。

字段索引（实测校准，vals 是按 `~` 切分后的列表）：
    1=name, 3=price, 4=last_close, 5=open, 31=change_amt, 32=change_pct,
    33=high, 34=low, 37=amount_wan, 38=turnover_pct, 39=pe_ttm,
    43=amplitude_pct（不是PB！）, 44=mcap_yi, 45=float_mcap_yi, 46=pb,
    47=limit_up, 48=limit_down, 49=vol_ratio, 52=pe_static
"""
from __future__ import annotations

import urllib.request
from collections.abc import Iterable

from .ticker import market_prefix, normalize_ticker

# 支持的腾讯财经前缀（A 股 + 港股 + 美股 + 指数）
_SUPPORTED_PREFIXES = ("sh", "sz", "bj", "hk", "us")


def _parse_quote_response(data: str) -> dict[str, dict]:
    """解析 qt.gtimg.cn 返回的多行响应。

    返回：{code: {name, price, pe_ttm, pb, mcap_yi, turnover_pct, ...}}
    code 段按前缀剥离（A 股 6 位，港股 5 位，美股字母如 AAPL）。
    """
    result: dict[str, dict] = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        matched_prefix: str | None = None
        for p in _SUPPORTED_PREFIXES:
            if key.startswith(p):
                matched_prefix = p
                break
        if not matched_prefix:
            continue
        code = key[len(matched_prefix):]

        def _f(idx: int, default: float = 0.0, _vals: list[str] = vals) -> float:
            try:
                v = _vals[idx]
                return float(v) if v else default
            except (ValueError, IndexError):
                return default

        result[code] = {
            "name": vals[1],
            "price": _f(3),
            "last_close": _f(4),
            "open": _f(5),
            "change_amt": _f(31),
            "change_pct": _f(32),
            "high": _f(33),
            "low": _f(34),
            "amount_wan": _f(37),
            "turnover_pct": _f(38),
            "pe_ttm": _f(39),
            "amplitude_pct": _f(43),
            "mcap_yi": _f(44),
            "float_mcap_yi": _f(45),
            "pb": _f(46),
            "limit_up": _f(47),
            "limit_down": _f(48),
            "vol_ratio": _f(49),
            "pe_static": _f(52),
        }
    return result


def tencent_quote(codes: Iterable[str]) -> dict[str, dict]:
    """批量拉取腾讯财经实时行情（A 股 sh/sz/bj 前缀；国际标的走 GlobalQuoteProvider）。"""
    normalized = [normalize_ticker(c) for c in codes if c]
    if not normalized:
        return {}

    prefixed = [f"{market_prefix(c)}{c}" for c in normalized]
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    resp = urllib.request.urlopen(req, timeout=10)
    data = resp.read().decode("gbk", errors="ignore")
    return _parse_quote_response(data)
