"""资金流层：东财 push2 资金流（分钟/日级）+ 龙虎榜席位聚合。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from ._shared import UA, eastmoney_datacenter, em_get, em_market_code, normalize_ticker

logger = logging.getLogger(__name__)


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
        # 网络/解析错误 → raise（DataRouter 走 fallback）
        raise RuntimeError(f"eastmoney_fund_flow_minute({code}) failed: {exc}") from exc

    rows: list[dict[str, Any]] = []
    for line in d.get("data", {}).get("klines", []):
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
        # 网络/解析错误 → raise（让 DataRouter 走 fallback / 调用方决定 strict）
        raise RuntimeError(f"dragon_tiger records query failed ({code}): {exc}") from exc
    for row in rows:
        records.append(
            {
                "date": str(row.get("TRADE_DATE", ""))[:10],
                "reason": row.get("EXPLANATION", ""),
                "net_buy": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
                "turnover": round(float(row.get("TURNOVERRATE") or 0), 2),
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

        for row in buy_data[:5]:
            seats["buy"].append(
                {
                    "name": row.get("OPERATEDEPT_NAME", ""),
                    "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                    "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                    "net": round((row.get("NET") or 0) / 10000, 1),
                }
            )
        for row in sell_data[:5]:
            seats["sell"].append(
                {
                    "name": row.get("OPERATEDEPT_NAME", ""),
                    "buy_amt": round((row.get("BUY") or 0) / 10000, 1),
                    "sell_amt": round((row.get("SELL") or 0) / 10000, 1),
                    "net": round((row.get("NET") or 0) / 10000, 1),
                }
            )

    # 机构买卖统计（OPERATEDEPT_CODE="0" 表示机构专用席位）
    institution = {"buy_amt": 0.0, "sell_amt": 0.0, "net_amt": 0.0}
    for row in buy_data:
        if str(row.get("OPERATEDEPT_CODE", "")) == "0":
            institution["buy_amt"] += (row.get("BUY") or 0)
    for row in sell_data:
        if str(row.get("OPERATEDEPT_CODE", "")) == "0":
            institution["sell_amt"] += (row.get("SELL") or 0)
    institution["buy_amt"] = round(institution["buy_amt"] / 10000, 1)
    institution["sell_amt"] = round(institution["sell_amt"] / 10000, 1)
    institution["net_amt"] = round(institution["buy_amt"] - institution["sell_amt"], 1)

    return {"records": records, "seats": seats, "institution": institution}
