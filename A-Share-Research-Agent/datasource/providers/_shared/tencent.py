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

# 支持的腾讯财经前缀（A 股 + 港股 + 美股 + 指数 + 海外期货）
_SUPPORTED_PREFIXES = ("sh", "sz", "bj", "hk", "us", "hf")

# 海外期货（hf_）字段格式：vals[0]=price, 1=change_pct, 2=open, 3=last_close,
# 4=high, 5=low, 6=time, 12=date, 13=name（GBK 中文）
_HF_FUTURES_NAME_OVERRIDE: dict[str, str] = {
    "GC": "纽约黄金 (COMEX Gold)",
    "CL": "WTI 原油 (WTI Crude)",
    "SI": "纽约白银 (COMEX Silver)",
    "S":  "美大豆 (Soybean)",
    "NG": "天然气 (Natural Gas)",
    "HG": "美铜 (Copper)",
    "HSI": "恒生指数期货",
}


def _parse_quote_response(data: str) -> dict[str, dict]:
    """解析 qt.gtimg.cn 返回的多行响应。

    返回：{code: {name, price, pe_ttm, pb, mcap_yi, turnover_pct, ...}}
    code 段按前缀剥离（A 股 6 位，港股 5 位，美股字母如 AAPL，期货 2-3 字母）。

    前缀分类：
    - sh/sz/bj/hk/us：标准 53 字段行情
    - hf：海外期货（14 字段紧凑格式）— 单独解析
    """
    result: dict[str, dict] = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        full_key = line.split("=")[0]  # e.g. "v_hf_GC" / "v_sh000001" / "v_hkHSI"
        vals_quote = line.split('"')[1]  # 引号内原文（不分隔，先判定再切）

        # 海外期货（hf_）走单独解析路径
        # 关键差异：A 股/港股/美股 53 字段用 "~" 分隔；海外期货 14 字段用 "," 分隔。
        is_futures = "_hf" in full_key
        if is_futures:
            vals = vals_quote.split(",")
            fut = full_key.split("_")[-1].upper()  # "GC" / "CL" / ...
            if fut and len(vals) >= 6:
                def _f(idx: int, default: float = 0.0) -> float:
                    try:
                        v = vals[idx]
                        return float(v) if v else default
                    except (ValueError, IndexError):
                        return default
                # 中文名优先级：override 映射 > vals[13]（腾讯 GBK 解码有时会乱码）
                # 用 override 作为主真值，只有 override 缺失时才退到 vals[13]。
                name = _HF_FUTURES_NAME_OVERRIDE.get(fut)
                if not name:
                    raw_name = vals[13] if len(vals) > 13 else fut
                    name = raw_name if (raw_name and not raw_name.isdigit()) else fut
                result[fut] = {
                    "name": name,
                    "price": _f(0),
                    "change_pct": _f(1),
                    "open": _f(2),
                    "last_close": _f(3),
                    "high": _f(4),
                    "low": _f(5),
                    "change_amt": 0.0,
                    "amount_wan": 0.0,
                    "turnover_pct": 0.0,
                    "pe_ttm": 0.0,
                    "amplitude_pct": 0.0,
                    "mcap_yi": 0.0,
                    "float_mcap_yi": 0.0,
                    "pb": 0.0,
                    "limit_up": 0.0,
                    "limit_down": 0.0,
                    "vol_ratio": 0.0,
                    "pe_static": 0.0,
                }
            continue

        vals = vals_quote.split("~")
        if len(vals) < 53:
            continue
        # 标准 53 字段前缀识别（A 股 / 港股 / 美股）：
        # 取 key 中形如 "_sh" / "_sz" / "_bj" / "_hk" / "_us" 的段
        matched_prefix: str | None = None
        for p in ("sh", "sz", "bj", "hk", "us"):
            if "_" + p in full_key:
                matched_prefix = p
                break
        if not matched_prefix:
            # 兜底：取末段直接尝试前缀
            last = full_key.split("_")[-1]
            for p in ("sh", "sz", "bj", "hk", "us"):
                if last.startswith(p):
                    matched_prefix = p
                    break
        if not matched_prefix:
            continue
        last = full_key.split("_")[-1]
        code = last[len(matched_prefix):]

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
