"""Fundamentals Extended Provider — 扩展基本面数据。

数据源：
- 东方财富数据中心：业绩预告/快报、机构持仓、分析师预期
- 同花顺 iFinD：一致预期、盈利预测

覆盖：
- 业绩预告/快报（提前捕捉盈利拐点）
- 机构持仓变动（北向/基金/社保/QFII）
- 分析师一致预期（目标价/盈利预测）
- 分红送配历史
- 限售解禁日历

适用场景：
- 基本面研究（盈利趋势 + 估值锚定）
- 事件驱动（业绩预告超预期 → 催化）
- 资金面研判（机构增减持 → 方向信号）
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from datasource.errors import ProviderError
from datasource.providers._http import http_get_json
from datasource.registry import register_provider

logger = logging.getLogger(__name__)

# 东财数据中心 API
_DC_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _to_secucode(code: str) -> str:
    """6 位代码 → 东财 SECUCODE（600519.SH / 000001.SZ / 830799.BJ）。"""
    code = code.strip().split(".")[0].zfill(6)
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


async def _dc_get(
    report_name: str,
    params: dict | None = None,
    timeout: float = 10,
    *,
    page_number: int = 1,
    page_size: int = 50,
    sort_columns: str = "",
) -> list[dict]:
    """东财数据中心通用查询（复用全局连接池）。

    Args:
        report_name: 数据集名（如 RPT_LIFT_STAGE）
        params:      额外查询参数，支持 "filter"（东财过滤语法）
        page_number: 页码（从 1 开始）
        page_size:   单页条数
        sort_columns: 排序列（空则用数据集默认排序）
    """
    base_params = {
        "reportName": report_name,
        "columns": "ALL",
        "pageNumber": str(page_number),
        "pageSize": str(page_size),
        "sortTypes": "-1",
        "source": "WEB",
        "client": "WEB",
        **({"filter": params["filter"]} if params and "filter" in params else {}),
    }
    if sort_columns:
        base_params["sortColumns"] = sort_columns
    data = await http_get_json(_DC_BASE, params=base_params, timeout=timeout)
    return (data.get("result") or {}).get("data") or []


@register_provider("fundamentals_ext")
class FundamentalsExtProvider:
    """扩展基本面数据 Provider。"""

    @property
    def name(self) -> str:
        return "fundamentals_ext"

    async def health_check(self) -> bool:
        return True

    def supported_methods(self) -> list[str]:
        from datasource.method_table import FULL_METHOD_TABLE

        return sorted(m for m, provs in FULL_METHOD_TABLE.items() if "fundamentals_ext" in provs)

    async def get_earnings_forecast(
        self,
        symbol: str = "",
        period: str = "",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取业绩预告/快报。

        Args:
            symbol: 股票代码（空=全市场最新）
            period: 报告期（如 "2025-06-30"），空=最新

        Returns:
            [{code, name, period, type, pct_change_min, pct_change_max, summary}, ...]
        """
        try:
            filter_str = ""
            if symbol:
                code = symbol.strip().split(".")[0]
                filter_str = f'(SECURITY_CODE="{code}")'
            if period:
                filter_str += f'(REPORT_DATE=\'{period}\')'

            items = await _dc_get(
                "RPT_PUBLIC_OP_NEWPREDICT",
                {"filter": filter_str} if filter_str else None,
            )
            return [
                {
                    "code": item.get("SECURITY_CODE", ""),
                    "name": item.get("SECURITY_NAME_ABBR", ""),
                    "period": item.get("REPORT_DATE", ""),
                    "type": item.get("PREDICT_FINANCE_CODE", ""),  # 预增/预减/扭亏/续亏
                    "pct_change_min": item.get("ADD_AMP_LOWER", ""),
                    "pct_change_max": item.get("ADD_AMP_UPPER", ""),
                    "summary": item.get("PREDICT_CONTENT", ""),
                    "published_at": item.get("NOTICE_DATE", ""),
                }
                for item in items[:30]
            ]
        except Exception as exc:
            raise ProviderError(
                f"业绩预告获取失败: {exc}",
                provider="fundamentals_ext", method="get_earnings_forecast", cause=exc,
            ) from exc

    async def get_institution_holdings(
        self,
        symbol: str,
        holder_type: str = "north",
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取机构持仓变动。

        Args:
            symbol: 股票代码
            holder_type: "north"=北向 "fund"=基金 "all"=全部

        Returns:
            [{holder_name, hold_amount, hold_ratio, change_amount, change_ratio, date}, ...]
        """
        code = symbol.strip().split(".")[0]
        try:
            if holder_type == "north":
                # 北向资金持仓
                items = await _dc_get(
                    "RPT_MUTUAL_HOLDSTOCKNORTH_STA",
                    {"filter": f'(SECURITY_CODE="{code}")'},
                )
            else:
                # 十大流通股东（北交所代码映射 .BJ，避免误拼 .SZ 查不到）
                items = await _dc_get(
                    "RPT_F10_EH_FREEHOLDERS",
                    {"filter": f'(SECUCODE="{_to_secucode(code)}")'},
                )

            return [
                {
                    "holder_name": item.get("HOLDER_NAME", item.get("FREE_HOLDER_NAME", "")),
                    "hold_amount": item.get("HOLD_SHARES", item.get("FREE_HOLD_NUM", 0)),
                    "hold_ratio": item.get("HOLD_RATIO", item.get("FREE_HOLD_RATIO", 0)),
                    "change_amount": item.get("CHANGE_SHARES", 0),
                    "date": item.get("TRADE_DATE", item.get("END_DATE", "")),
                    "type": holder_type,
                }
                for item in items[:20]
            ]
        except Exception as exc:
            raise ProviderError(
                f"机构持仓获取失败({symbol}): {exc}",
                provider="fundamentals_ext", method="get_institution_holdings", cause=exc,
            ) from exc

    async def get_analyst_consensus(
        self,
        symbol: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """获取分析师一致预期。

        Args:
            symbol: 股票代码

        Returns:
            {
                "code": "600519",
                "rating": "买入",
                "target_price": 1800.0,
                "eps_forecast": {"2025": 65.2, "2026": 72.1},
                "report_count": 25,
                "latest_reports": [{title, org, date, rating}, ...]
            }
        """
        code = symbol.strip().split(".")[0]
        result: dict[str, Any] = {
            "code": code,
            "rating": "",
            "target_price": None,
            "eps_forecast": {},
            "report_count": 0,
            "latest_reports": [],
        }
        try:
            # 东财研报中心
            items = await _dc_get(
                "RPT_RATADJUST_LIST",
                {"filter": f'(SECURITY_CODE="{code}")'},
            )
            if items:
                result["report_count"] = len(items)
                result["latest_reports"] = [
                    {
                        "title": item.get("TITLE", ""),
                        "org": item.get("ORG_NAME_ABBR", ""),
                        "date": item.get("NOTICE_DATE", ""),
                        "rating": item.get("EM_RATING_NAME", ""),
                    }
                    for item in items[:5]
                ]
                # 最新评级
                result["rating"] = items[0].get("EM_RATING_NAME", "")
        except Exception as exc:
            logger.debug("分析师预期获取失败(%s): %s", code, exc)

        return result

    async def get_dividend_history(
        self,
        symbol: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取分红送配历史。

        Returns:
            [{year, plan, ex_date, dividend_per_share, bonus_ratio}, ...]
        """
        code = symbol.strip().split(".")[0]
        secucode = _to_secucode(code)
        try:
            items = await _dc_get(
                "RPT_SHAREBONUS_DET",
                {"filter": f'(SECUCODE="{secucode}")'},
            )
            return [
                {
                    "year": item.get("REPORT_DATE", ""),
                    "plan": item.get("IMPL_PLAN_PROFILE", ""),
                    "ex_date": item.get("EX_DIVIDEND_DATE", ""),
                    "dividend_per_share": item.get("PRETAX_BONUS_RMB", 0),
                    "bonus_ratio": item.get("BONUS_IT_RATIO", 0),
                }
                for item in items[:10]
            ]
        except Exception as exc:
            raise ProviderError(
                f"分红历史获取失败({symbol}): {exc}",
                provider="fundamentals_ext", method="get_dividend_history", cause=exc,
            ) from exc

    async def get_lockup_calendar(
        self,
        days_ahead: int = 30,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """获取限售解禁日历（未来 N 天）。

        服务端按日期区间过滤 + 分页拉取，避免"只取第一页全市场再本地过滤"
        在解禁密集日漏票的问题。

        Returns:
            [{code, name, unlock_date, unlock_shares, unlock_ratio, days_ahead}, ...]
        """
        try:
            days_ahead = max(1, min(int(days_ahead), 365))
            today = date.today()
            end = date.fromordinal(today.toordinal() + days_ahead)
            # 服务端按解禁日区间过滤（含当天），再按解禁日升序取最近的先返回
            date_filter = (
                f"(FREE_DATE>='{today.isoformat()}')"
                f"(FREE_DATE<='{end.isoformat()}')"
            )
            results: list[dict[str, Any]] = []
            seen: set[tuple[str, str]] = set()
            max_pages = 10  # 单页 100 条 × 10 页 = 1000 条上限，防极端情况无限翻页
            page_size = 100
            for page in range(1, max_pages + 1):
                items = await _dc_get(
                    "RPT_LIFT_STAGE",
                    {"filter": date_filter},
                    page_number=page,
                    page_size=page_size,
                    sort_columns="FREE_DATE",
                )
                if not items:
                    break
                for item in items:
                    unlock_str = item.get("FREE_DATE", "")
                    if not unlock_str:
                        continue
                    try:
                        unlock_date = date.fromisoformat(unlock_str[:10])
                        delta = (unlock_date - today).days
                    except (ValueError, TypeError):
                        continue
                    if not (0 <= delta <= days_ahead):
                        continue
                    key = (item.get("SECURITY_CODE", ""), unlock_str[:10])
                    if key in seen:
                        continue  # 去重（分页边界可能出现重复行）
                    seen.add(key)
                    results.append({
                        "code": item.get("SECURITY_CODE", ""),
                        "name": item.get("SECURITY_NAME_ABBR", ""),
                        "unlock_date": unlock_str[:10],
                        "unlock_shares": item.get("FREE_NUM", 0),
                        "unlock_ratio": item.get("FREE_RATIO", 0),
                        "days_ahead": delta,
                    })
                # 不足一页说明已到末尾
                if len(items) < page_size:
                    break
            return sorted(results, key=lambda x: (x["days_ahead"], x["code"]))[:30]
        except Exception as exc:
            raise ProviderError(
                f"解禁日历获取失败: {exc}",
                provider="fundamentals_ext", method="get_lockup_calendar", cause=exc,
            ) from exc
