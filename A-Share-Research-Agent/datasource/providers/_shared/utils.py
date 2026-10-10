"""datasource.providers._shared.utils — 通用小工具（纯函数，无 I/O）。

避免 hk_full / us_stock / bse / futures 等 provider 各自重复定义 `_fmt`。
"""
from __future__ import annotations

from datetime import date
from numbers import Number
from typing import Any


def fmt_date(d: str | date | None) -> str:
    """把日期参数归一化为 YYYYMMDD 字符串（'' 表示未提供）。

    Args:
        d: 支持 "2025-07-01" / "20250701" / datetime.date / None
    """
    if d is None:
        return ""
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")


def to_native(v: Any) -> Any:
    """外部数据源返回值归一为原生 Python 类型。

    用 numbers.Number 判断而非 isinstance(v, (int, float))：
    numpy 标量（np.int64/np.float32 等）不是 Python int/float 的子类，
    旧写法会把数值误判成字符串类型返回，污染下游契约。
    """
    if v is None:
        return None
    if isinstance(v, bool):  # bool 是 Number 子类，但业务上不应转 float
        return v
    if isinstance(v, Number):
        return float(v)
    return str(v)


# ── 巨潮（cninfo）市场参数 ─────────────────────────────────────────
# 巨潮按市场分库查询：column/plate 必须与股票所在市场一致，否则查不到。
# 按代码前缀判市场（orgid 命名不统一：gssh/gssz/gfbj/纯数字混用，不可依赖）。
CNINFO_MARKET_PARAMS: tuple[tuple[str, str, str], ...] = (
    # (代码前缀, column, plate)
    ("6", "sse", "sh;shmb;shkcp"),
    ("9", "sse", "sh;shmb;shkcp"),
    ("0", "szse", "sz;szmb;szzx;szcn"),
    ("3", "szse", "sz;szmb;szzx;szcn"),
    ("4", "bse", "bj"),
    ("8", "bse", "bj"),
)
CNINFO_DEFAULT_MARKET = ("szse", "sz;szmb;szzx;szcn")
# 全市场查询用（symbol 为空时遍历三个市场合并）
CNINFO_ALL_MARKETS: tuple[tuple[str, str], ...] = (
    ("sse", "sh;shmb;shkcp"),
    ("szse", "sz;szmb;szzx;szcn"),
    ("bse", "bj"),
)


def cninfo_market_params(code: str) -> tuple[str, str]:
    """6 位代码 → 巨潮 (column, plate)。"""
    code = (code or "").strip()
    for prefix, column, plate in CNINFO_MARKET_PARAMS:
        if code.startswith(prefix):
            return column, plate
    return CNINFO_DEFAULT_MARKET


# K 线列名别名表：规范名 -> 候选名（含中文/英文常见变体，顺序即优先级）
KLINE_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "date":   ("date", "日期", "time", "datetime", "trade_date", "tradedate", "day"),
    "open":   ("open", "开盘", "开盘价", "open_price"),
    "high":   ("high", "最高", "最高价", "high_price"),
    "low":    ("low", "最低", "最低价", "low_price"),
    "close":  ("close", "收盘", "收盘价", "close_price"),
    "volume": ("volume", "vol", "成交量", "volume_value"),
}


def standardize_kline_df(df, *, keep_extra: bool = True):
    """把常见 K 线列名（中文/英文变体）归一化为标准列 date/open/high/low/close/volume。

    - 只对别名表中出现的列做重命名；找不到候选的规范列（如 data 只有"成交额"无标准对应）不新增空列。
    - keep_extra=True（默认）时保留其余列原样；False 时只保留 6 个标准列。
    - 返回新 DataFrame，不改动原 df（用 df.rename(columns=...) 天然返回新对象）。
    """
    rename_map = {}
    for canonical, candidates in KLINE_COLUMN_ALIASES.items():
        for cand in candidates:
            if cand in df.columns:
                rename_map[cand] = canonical
                break
    out = df.rename(columns=rename_map)
    return out if keep_extra else out[[c for c in ("date", "open", "high", "low", "close", "volume") if c in out.columns]]
