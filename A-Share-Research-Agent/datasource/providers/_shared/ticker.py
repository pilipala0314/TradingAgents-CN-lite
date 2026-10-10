"""Ticker 归一化 + 市场前缀（纯函数，无 I/O）。

支持的输入格式：
- "688017"
- "SH688017" / "sh688017"
- "688017.SH" / "688017.sh"
- "SZ000001" / "BJ832000"
- "sh688017" / "sz000001" / "bj832000"
"""
from __future__ import annotations


def normalize_ticker(code: str) -> str:
    """归一化 6 位股票代码 → '688017' 形式。

    支持的输入格式：
    - "688017"
    - "SH688017" / "sh688017" / "688017.SH" / "688017.sh"
    - "SZ000001" / "BJ832000" / "sz000001" / "bj832000"

    失败：空串。
    """
    if not code:
        return ""
    c = code.strip().upper()
    # 后缀形式 "688017.SH" → 先剥市场后缀（须先于前缀判断，否则数字挡住前缀匹配）
    for suffix in ("SH", "SZ", "BJ"):
        if c.endswith("." + suffix):
            c = c[: -len(suffix) - 1]
            break
    # 前缀形式 "SH688017" → 剥市场前缀
    if c.startswith(("SH", "SZ", "BJ")):
        c = c[2:]
    return c


def market_prefix(code: str) -> str:
    """6 位代码 → 市场前缀（用于腾讯 / 东财 secid 构造）。

    规则：4/8/92 → bj（北交所 43/83/87/920 段）；6/9 → sh（注意 92 需先判）；
    其他 → sz。与 sina 的 _to_sina_symbol 保持一致。
    """
    if code.startswith("92"):
        return "bj"
    if code.startswith(("4", "8")):
        return "bj"
    if code.startswith(("6", "9")):
        return "sh"
    return "sz"


def em_market_code(code: str) -> int:
    """6 位代码 → 东财 secid 段：沪 1 / 深 0 / 京 0（东财京交所复用 0）。"""
    return 1 if code.startswith(("6", "9")) else 0
