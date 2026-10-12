"""资金流层：东财 push2 资金流（分钟/日级）+ 龙虎榜席位聚合。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from datasource.providers._http import safe_float
from datasource.providers._shared import UA, eastmoney_datacenter, em_get, em_market_code, normalize_ticker

logger = logging.getLogger(__name__)


def _to_wan(value: Any) -> float:
    """金额（元，容忍字符串/None/异常值）→ 万元（保留 1 位小数）。

    东财接口某些字段异常时会返回 "-" 等占位符，裸 float()/除法会抛异常，
    统一走 safe_float 兜底为 0.0。
    """
    return round(safe_float(value) / 10000, 1)


def fetch_fund_flow_minute(code: str) -> list[dict[str, Any]]:
    """盘中实时分钟级资金流（主力/大单/中单/小单/超大单净流入）。

    单位: 元。klt=1 分钟级，klt=101 日级。
    返回: [{time, main_net, small_net, mid_net, large_net, super_net}, ...]

    失败语义：网络/解析错误 → raise；成功但无分钟数据 → 返 []。
    """
    code = normalize_ticker(code)
    market_code = em_market_code(code)
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {
        "secid": f"{market_code}.{code}",
        "klt": 1,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Origin": "https://quote.eastmoney.com",
    }
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        d = r.json()
    except Exception as exc:
        # 网络/解析错误 → raise（DataSource 走 fallback）
        raise RuntimeError(f"eastmoney_fund_flow_minute({code}) failed: {exc}") from exc

    rows: list[dict[str, Any]] = []
    for line in (d.get("data") or {}).get("klines", []):
        parts = line.split(",")
        if len(parts) >= 6:
            try:
                rows.append(
                    {
                        "time": parts[0],
                        "main_net": float(parts[1]),
                        "small_net": float(parts[2]),
                        "mid_net": float(parts[3]),
                        "large_net": float(parts[4]),
                        "super_net": float(parts[5]),
                    }
                )
            except ValueError:
                continue
    return rows


def fetch_dragon_tiger(code: str, trade_date: str | None = None, look_back: int = 30) -> dict[str, Any]:
    """龙虎榜聚合：上榜记录 + 买卖席位 TOP5 + 机构买卖统计。

    trade_date: YYYY-MM-DD；None 表示取最新可查日。
    look_back: 回看天数。
    返回: {records, seats: {buy, sell}, institution: {buy_amt, sell_amt, net_amt}}
    """
    code = normalize_ticker(code)
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
    start_str = start.strftime("%Y-%m-%d")

    records: list[dict[str, Any]] = []
    try:
        rows = eastmoney_datacenter(
            "RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_str=(
                f"(TRADE_DATE>='{start_str}')"
                f"(TRADE_DATE<='{trade_date}')"
                f"(SECURITY_CODE=\"{code}\")"
            ),
            page_size=50,
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
    except Exception as exc:
        # 网络/解析错误 → raise（让 DataSource 走 fallback / 调用方决定 strict）
        raise RuntimeError(f"dragon_tiger records query failed ({code}): {exc}") from exc
    for row in rows:
        records.append(
            {
                "date": str(row.get("TRADE_DATE", ""))[:10],
                "reason": row.get("EXPLANATION", ""),
                "net_buy": _to_wan(row.get("BILLBOARD_NET_AMT")),
                "turnover": round(safe_float(row.get("TURNOVERRATE")), 2),
            }
        )

    seats: dict[str, list[dict[str, Any]]] = {"buy": [], "sell": []}
    buy_data: list[dict[str, Any]] = []
    sell_data: list[dict[str, Any]] = []
    if records:
        latest_date = records[0]["date"]
        try:
            buy_data = eastmoney_datacenter(
                "RPT_BILLBOARD_DAILYDETAILSBUY",
                filter_str=f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
                page_size=10,
                sort_columns="BUY",
                sort_types="-1",
            )
        except Exception as exc:
            raise RuntimeError(f"dragon_tiger buy query failed ({code}): {exc}") from exc
        try:
            sell_data = eastmoney_datacenter(
                "RPT_BILLBOARD_DAILYDETAILSSELL",
                filter_str=f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
                page_size=10,
                sort_columns="SELL",
                sort_types="-1",
            )
        except Exception as exc:
            raise RuntimeError(f"dragon_tiger sell query failed ({code}): {exc}") from exc

        seats["buy"] = _build_seat_list(buy_data)
        seats["sell"] = _build_seat_list(sell_data)

    # 机构买卖统计（OPERATEDEPT_CODE="0" 表示机构专用席位）
    institution = {"buy_amt": 0.0, "sell_amt": 0.0, "net_amt": 0.0}
    for row in buy_data:
        if str(row.get("OPERATEDEPT_CODE", "")) == "0":
            institution["buy_amt"] += safe_float(row.get("BUY"))
    for row in sell_data:
        if str(row.get("OPERATEDEPT_CODE", "")) == "0":
            institution["sell_amt"] += safe_float(row.get("SELL"))
    institution["buy_amt"] = round(institution["buy_amt"] / 10000, 1)
    institution["sell_amt"] = round(institution["sell_amt"] / 10000, 1)
    institution["net_amt"] = round(institution["buy_amt"] - institution["sell_amt"], 1)

    return {"records": records, "seats": seats, "institution": institution}


def _build_seat_list(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把龙虎榜买卖席位行组装为统一 seat dict（金额单位: 万元）。

    字段：name / buy_amt / sell_amt / net。取前 5 条席位。
    """
    out: list[dict[str, Any]] = []
    for row in rows[:5]:
        out.append(
            {
                "name": row.get("OPERATEDEPT_NAME", ""),
                "buy_amt": _to_wan(row.get("BUY")),
                "sell_amt": _to_wan(row.get("SELL")),
                "net": _to_wan(row.get("NET")),
            }
        )
    return out
