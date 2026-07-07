"""Sector Heatmap Service — 申万行业指数热力图矩阵（P0-T12）。

目的：
- 给前端 signals tab ② 板块轮动 section 提供 31 行业 × 5 日 涨幅矩阵
- 数据契约：rows=[{code, name, data:[{date, change_pct, close, amount}]}]

数据源：
- SWIndustryProvider.get_index_list() → 31 行业
- SWIndustryProvider.get_daily_bars(code, start, end) → 每行业 K 线

设计：
- 输入 provider 抽象（duck typing），便于测试 mock
- 容错：单行业失败不影响其他行业，记 logger.warning
- n_days 默认 5（plan 要求），但允许覆盖
- 输出列按日期升序（最旧在左，最新在右）
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _coerce_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _normalize_bar_dict(raw: dict[str, Any]) -> dict[str, Any] | None:
    """把 akshare 返回的 dict 归一化为 {date, close, change_pct, amount}。

    akshare stock_board_industry_hist_em 列名（中文）：
    - 日期 / 开盘 / 收盘 / 最高 / 最低 / 成交量 / 成交额 / 振幅 / 涨跌幅 / 涨跌额 / 换手率

    兼容旧版（列名为 '日期' '收盘价' '涨跌幅' 等）。
    """
    if not isinstance(raw, dict):
        return None
    # 日期归一化
    d_val = raw.get("date") or raw.get("日期") or raw.get("trade_date")
    if d_val is None:
        return None
    if isinstance(d_val, datetime):
        d_str = d_val.strftime("%Y-%m-%d")
    elif isinstance(d_val, date):
        d_str = d_val.isoformat()
    else:
        s = str(d_val).strip()
        # '20260707' → '2026-07-07'
        d_str = f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s[:10]
    close = _coerce_float(raw.get("close") if raw.get("close") is not None else raw.get("收盘") if raw.get("收盘") is not None else raw.get("收盘价"))
    pct_raw = (
        raw.get("change_pct")
        if raw.get("change_pct") is not None
        else raw.get("涨跌幅")
        if raw.get("涨跌幅") is not None
        else raw.get("pct_change")
    )
    pct = _coerce_float(pct_raw)
    amount = _coerce_float(
        raw.get("amount")
        if raw.get("amount") is not None
        else raw.get("成交额")
        if raw.get("成交额") is not None
        else raw.get("成交金额")
    )
    if close is None and pct is None:
        return None
    return {"date": d_str, "close": close, "change_pct": pct, "amount": amount}


def _select_last_n_by_date(bars: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """取最近 n 个交易日的 bars（按 date 升序）。"""
    cleaned = [b for b in (bars or []) if b is not None]
    cleaned.sort(key=lambda b: b.get("date", ""))
    return cleaned[-n:] if n > 0 else cleaned


async def build_sector_heatmap(
    provider: Any,
    *,
    n_days: int = 5,
    lookback_calendar_days: int = 30,
    end_date: str | None = None,
) -> dict[str, Any]:
    """构建板块热力图矩阵。

    Args:
        provider                SWIndustryProvider 实例（duck typing）
        n_days                  矩阵列数（交易日数），默认 5
        lookback_calendar_days  向后回看的日历天数（覆盖 n_days 个交易日）
        end_date                截止日期 YYYY-MM-DD（默认今天）

    Returns:
        {
          "matrix": [
            {"code": "801010", "name": "农林牧渔", "values": [pct1, pct2, ...], "dates": ["2026-07-01", ...]},
            ...
          ],
          "dates": [...],  # 全局日期列（升序）
          "industry_count": 31,
          "generated_at": "2026-07-07T20:00:00+08:00",
        }
    """
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=lookback_calendar_days)
    start_date = start_dt.strftime("%Y%m%d")
    end_date_compact = end_dt.strftime("%Y%m%d")

    # 1. 拉 31 行业列表
    try:
        list_df = await provider.get_index_list()
    except Exception as exc:
        logger.exception("sector_heatmap: get_index_list 失败")
        return _empty_heatmap(n_days=n_days, reason=f"list failed: {exc}")

    if list_df is None or len(list_df) == 0:
        return _empty_heatmap(n_days=n_days, reason="list empty")

    # 2. 逐行业拉 K 线（顺序请求，akshare 是同步）
    rows: list[dict[str, Any]] = []
    for _, row in list_df.iterrows():
        code = str(row.get("code", "")).strip()
        name = str(row.get("name", "")).strip()
        if not code:
            continue
        try:
            df = await provider.get_daily_bars(
                symbol=code, start_date=start_date, end_date=end_date_compact,
            )
        except Exception as exc:
            logger.warning(
                "sector_heatmap: get_daily_bars 失败 code=%s err=%s", code, exc,
            )
            rows.append({"code": code, "name": name, "values": [None] * n_days, "raw": []})
            continue
        if df is None or len(df) == 0:
            rows.append({"code": code, "name": name, "values": [None] * n_days, "raw": []})
            continue
        bars = [_normalize_bar_dict(rec) for rec in df.to_dict(orient="records")]
        last_n = _select_last_n_by_date([b for b in bars if b], n_days)
        rows.append({
            "code": code,
            "name": name,
            "values": [b.get("change_pct") for b in last_n],
            "dates": [b.get("date") for b in last_n],
            "amounts": [b.get("amount") for b in last_n],
            "raw": last_n,
        })

    # 3. 对齐日期列（取所有行最长的日期集合，按时间升序）
    all_dates: list[str] = []
    for r in rows:
        for d in r.get("dates") or []:
            if d and d not in all_dates:
                all_dates.append(d)
    all_dates.sort()
    # 截取最后 n_days 个
    if len(all_dates) > n_days:
        all_dates = all_dates[-n_days:]
    # 重新对齐每行的 values
    for r in rows:
        rd = r.get("dates") or []
        rv = r.get("values") or []
        aligned = []
        for d in all_dates:
            try:
                idx = rd.index(d)
                aligned.append(rv[idx])
            except ValueError:
                aligned.append(None)
        r["values"] = aligned
        r["dates"] = list(all_dates)
        r.pop("raw", None)
        r.pop("amounts", None)

    return {
        "matrix": rows,
        "dates": all_dates,
        "industry_count": len(rows),
        "generated_at": datetime.now().isoformat(),
    }


def _empty_heatmap(*, n_days: int, reason: str) -> dict[str, Any]:
    return {
        "matrix": [],
        "dates": [],
        "industry_count": 0,
        "generated_at": datetime.now().isoformat(),
        "reason": reason,
    }
