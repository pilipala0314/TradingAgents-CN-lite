"""provider_adapters.py — 各 provider 方法的 adapter 集合。

把 facade / agent 层用的"标准签名"翻译成各 provider 的"本地签名"，
让 DataRouter 兜底链能透明切换。

## 设计原则

- **零硬编码**：每个 adapter 是纯函数，可在 DataRouter._METHOD_ADAPTERS 注册
- **方向一致**：标准签名 → 本地签名（不反向）
- **优雅失败**：adapter 出错由 DataRouter 走 fallback 链兜底

## 标准签名约定

| method                | 标准签名                                                     | 说明 |
| --------------------- | ------------------------------------------------------------ | ---- |
| `get_daily_bars`      | `(symbol, start_date, end_date, adjust="")`                  | YYYYMMDD 字符串 |
| `get_minute_bars`     | `(symbol, start_datetime, end_datetime, period="1", adjust="")` | YYYYMMDDHHMMSS |
| `get_tencent_quote`   | `(codes: list[str])`                                         | 批量 |
| `get_kline`           | `(code, frequency, offset)`                                  | mootdx 风格 |
"""
from __future__ import annotations

from datetime import date, timedelta

# ── get_daily_bars 系列 ─────────────────────────────────────────────

def _akshare_daily_bars_adapter(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
    """标准签名 → akshare 本地签名。

    标准：(symbol, start_date, end_date, adjust="")
    兼容 legacy dashboard 签名：
    - `(symbol, datalen=60)`
    - `(symbol, start_date, end_date, adjust="")`
    """
    if not args and "symbol" not in kwargs:
        return args, kwargs
    symbol = args[0] if args else kwargs.get("symbol", "")
    if "datalen" in kwargs and "start_date" not in kwargs and len(args) <= 1:
        datalen = max(int(kwargs.get("datalen", 60) or 60), 1)
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=max(datalen * 2, 10))).strftime("%Y%m%d")
        return (symbol,), {"start_date": start, "end_date": end, "adjust": kwargs.get("adjust", "")}
    return args, kwargs


def _sina_daily_bars_adapter(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
    """标准签名 → sina 本地签名。

    标准：(symbol, start_date, end_date, adjust="")
    sina：get_daily_bars(symbol, scale, datalen) — sina API 只支持
        "返回最近 N 根 K 线"，不支持 date range。所以：
        1. 把 start_date → datalen（按日历日，截断到 500）
        2. 把 start_date / end_date 透传为 kwargs，sina provider 内部 post-filter
    """
    if not args and "symbol" not in kwargs:
        return args, kwargs
    symbol = args[0] if args else kwargs.get("symbol", "")
    start_date = args[1] if len(args) > 1 else kwargs.get("start_date", "")
    end_date = args[2] if len(args) > 2 else kwargs.get("end_date", "")
    # 推算 datalen（按日历日，含周末）
    try:
        if start_date and end_date:
            sd = _parse_yyyymmdd(start_date)
            ed = _parse_yyyymmdd(end_date)
            days = max((ed - sd).days + 1, 1)
            datalen = min(max(days, 5), 500)
        else:
            datalen = 60  # 默认 60 天
    except (ValueError, TypeError):
        datalen = 60
    new_kwargs: dict = {}
    if start_date:
        new_kwargs["start_date"] = start_date
    if end_date:
        new_kwargs["end_date"] = end_date
    return (symbol, 240, datalen), new_kwargs


def _netease_daily_bars_adapter(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
    """标准签名 → netease 本地签名。

    标准：(symbol, start_date, end_date, adjust="")
    netease：get_daily_bars(symbol, start, end, adjust) — kwargs 名是 start / end，
        不是 start_date / end_date。这里做重命名。
    """
    if not args and "symbol" not in kwargs:
        return args, kwargs
    symbol = args[0] if args else kwargs.get("symbol", "")
    start_date = args[1] if len(args) > 1 else kwargs.get("start_date", "")
    end_date = args[2] if len(args) > 2 else kwargs.get("end_date", "")
    adjust = args[3] if len(args) > 3 else kwargs.get("adjust", "")
    return (symbol, start_date, end_date, adjust), {}


# ── get_minute_bars 系列 ────────────────────────────────────────────

def _akshare_minute_bars_adapter(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
    """标准签名 → akshare 本地签名。

    标准：(symbol, start_datetime, end_datetime, period="1", adjust="")
    akshare minute 端点 `ak.stock_zh_a_hist_min_em` 实际接
    `start_date="YYYY-MM-DD HH:MM:SS"`（实测），但本项目契约是
    `YYYYMMDDHHMMSS` — 这里做归一化。

    接受格式（自动 detect）：
    - `20240102093000`     → `2024-01-02 09:30:00`
    - `2024-01-02 09:30:00` → 原样
    - `2024-01-02`         → `2024-01-02 09:30:00`（默认 09:30 开盘）
    """
    new_args = list(args) if args else []
    new_kwargs = dict(kwargs) if kwargs else {}

    def _norm(idx: int, key: str) -> None:
        val: str | None = None
        if idx is not None and idx < len(new_args):
            val = new_args[idx]
        elif key in new_kwargs:
            val = new_kwargs[key]
        if not val:
            return
        s = str(val).strip()
        # 已是 "YYYY-MM-DD HH:MM:SS" 形式
        if "-" in s and (" " in s or "T" in s):
            s = s.replace("T", " ")
            # 缺时间补 09:30:00
            if len(s) == 10:  # "2024-01-02"
                s = f"{s} 09:30:00"
            normalized = s
        # 纯数字 YYYYMMDDHHMMSS / YYYYMMDD
        elif s.isdigit():
            if len(s) == 8:  # YYYYMMDD
                normalized = f"{s[:4]}-{s[4:6]}-{s[6:8]} 09:30:00"
            elif len(s) == 12:  # MMDDHHMMSS — 不规范，但兜底
                normalized = f"20{s[:2]}-{s[2:4]}-{s[4:6]} {s[6:8]}:{s[8:10]}:{s[10:12]}"
            elif len(s) == 14:  # YYYYMMDDHHMMSS
                normalized = f"{s[:4]}-{s[4:6]}-{s[6:8]} {s[8:10]}:{s[10:12]}:{s[12:14]}"
            else:
                normalized = s  # 格式未知，透传
        else:
            normalized = s
        if idx is not None and idx < len(new_args):
            new_args[idx] = normalized
        else:
            new_kwargs[key] = normalized

    _norm(1, "start_datetime")
    _norm(2, "end_datetime")
    return tuple(new_args), new_kwargs


def _sina_minute_bars_adapter(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
    """标准签名 → sina 本地签名。

    标准：(symbol, start_datetime, end_datetime, period="1", adjust="")
    sina：get_minute_bars(symbol, scale, datalen)
        - period "1" → scale=1, "5" → scale=5, "15" → scale=15, "30" → scale=30, "60" → scale=60
        - sina 不接 end_datetime，统一用 datalen=240（4 小时交易时间 / 1min bar = 1 个交易日）
    """
    symbol = args[0] if args else kwargs.get("symbol", "")
    period = args[3] if len(args) > 3 else kwargs.get("period", "1")
    # 推算 datalen：240 根 1min = 4 小时交易时间
    period_to_datalen = {"1": 240, "5": 240, "15": 240, "30": 240, "60": 240}
    datalen = period_to_datalen.get(str(period), 240)
    scale = _period_to_scale(period)
    return (symbol, scale, datalen), {}


def _netease_minute_bars_adapter(args: tuple, kwargs: dict) -> tuple[tuple, dict]:
    """标准签名 → netease 本地签名。

    标准：(symbol, start_datetime, end_datetime, period="1", adjust="")
    netease：get_minute_bars(symbol, period, datalen)
    """
    symbol = args[0] if args else kwargs.get("symbol", "")
    period = args[3] if len(args) > 3 else kwargs.get("period", "1")
    return (symbol, period, 240), {}


# ── 工具 ───────────────────────────────────────────────────────────

def _parse_yyyymmdd(s: str | None) -> date:
    """解析 YYYYMMDD 或 YYYY-MM-DD 字符串。"""
    if not s:
        return date.today()
    s = str(s).strip().replace("-", "").replace("/", "")
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _period_to_scale(period: str | int) -> int:
    """period string/int → sina scale (1/5/15/30/60)."""
    try:
        p = int(period)
    except (ValueError, TypeError):
        p = 1
    if p in (1, 5, 15, 30, 60):
        return p
    return 1


# ── 工厂：批量注册到 DataRouter._METHOD_ADAPTERS ─────────────────

def register_all_adapters() -> None:
    """一次性注册所有 adapter 到 DataRouter._METHOD_ADAPTERS。

    DataRouter 在 __init__ 时会查这个 dict 自动包装。
    """
    from data_pipeline.data_router import DataRouter

    # get_daily_bars
    DataRouter._METHOD_ADAPTERS[("get_daily_bars", "akshare")] = _akshare_daily_bars_adapter
    DataRouter._METHOD_ADAPTERS[("get_daily_bars", "sina")] = _sina_daily_bars_adapter
    DataRouter._METHOD_ADAPTERS[("get_daily_bars", "netease")] = _netease_daily_bars_adapter

    # get_minute_bars
    DataRouter._METHOD_ADAPTERS[("get_minute_bars", "akshare")] = _akshare_minute_bars_adapter
    DataRouter._METHOD_ADAPTERS[("get_minute_bars", "sina")] = _sina_minute_bars_adapter
    DataRouter._METHOD_ADAPTERS[("get_minute_bars", "netease")] = _netease_minute_bars_adapter


# 注意：不在模块顶层调用 register_all_adapters()。
# 该函数会 `from data_pipeline.data_router import DataRouter`，如果顶层调用会在
# data_router.py 加载过程中触发循环导入（DataRouter 还未定义）。
# 改由 data_router.py 在 DataRouter 类定义完成之后显式调用。
