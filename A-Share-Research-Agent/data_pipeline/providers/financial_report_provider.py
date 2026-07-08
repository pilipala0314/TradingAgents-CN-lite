"""FinancialReportProvider — 财务关键指标数据接入。

数据源：akshare
- `stock_financial_analysis_indicator_em` — 主要财务指标（按报告期）
- `stock_zyjs_ths` — 估值（PE/PB 等，本 provider 不包含，估值见 ValuationProvider）

设计：
- 一次拉取返回该股最近 N 个报告期的关键指标长表
- 列名归一化：日期 / 指标 / 值
- 失败语义：网络错透传，列缺失字段填 None
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from data_pipeline.providers.akshare_provider import AkshareProvider

logger = logging.getLogger(__name__)


# 主要财务指标标准列名映射（akshare 中文列 → 标准 code / name）
# 精简版：保留最常用的 12 个指标
_INDICATOR_MAP: dict[str, tuple[str, str, str]] = {
    # 中文列名 → (code, english_name, unit)
    "基本每股收益": ("EPS", "earnings_per_share", "yuan"),
    "每股净资产": ("BVPS", "book_value_per_share", "yuan"),
    "加权平均净资产收益率(%)": ("ROE", "weighted_roe", "pct"),
    "毛利率(%)": ("GROSS_MARGIN", "gross_margin", "pct"),
    "净利率(%)": ("NET_MARGIN", "net_margin", "pct"),
    "营业总收入": ("REVENUE", "operating_revenue", "yuan"),
    "营业总成本": ("TOTAL_COST", "operating_cost", "yuan"),
    "营业利润": ("OPERATING_PROFIT", "operating_profit", "yuan"),
    "利润总额": ("TOTAL_PROFIT", "total_profit", "yuan"),
    "净利润": ("NET_PROFIT", "net_profit", "yuan"),
    "资产负债率(%)": ("DEBT_RATIO", "debt_ratio", "pct"),
    "总资产": ("TOTAL_ASSETS", "total_assets", "yuan"),
}


class FinancialReportProvider:
    """财报关键指标 Provider — DataRouter 的 'cn_financial_indicator' provider。"""

    def __init__(self, akshare: AkshareProvider | None = None) -> None:
        self._ak = akshare or AkshareProvider()

    async def get_indicators(
        self,
        symbol: str,
        *,
        start_year: str | None = None,
        end_year: str | None = None,
    ) -> pd.DataFrame:
        """拉取该股主要财务指标。

        Args:
            symbol:       6 位 A 股代码
            start_year:   起始年份（4 位字符串，如 "2020"）；None=不限
            end_year:     结束年份；None=不限

        Returns:
            DataFrame(columns=[
                "report_period", "indicator_code", "indicator_name",
                "value", "unit", "source",
            ])
        """
        try:
            logger.info("正在拉取主要财务指标: %s", symbol)
            df = await self._ak._run_with_retry(
                self._ak._ensure_akshare().stock_financial_analysis_indicator_em,
                symbol=symbol,
                start_year=start_year or "2020",
                end_year=end_year or "2025",
            )
        except Exception as e:
            logger.error("拉取财务指标失败: symbol=%s error=%s", symbol, e)
            raise

        if df is None or df.empty:
            return pd.DataFrame(
                columns=["report_period", "indicator_code", "indicator_name", "value", "unit", "source"]
            )

        return _flatten_indicators(df, source="akshare.stock_financial_analysis_indicator_em")

    async def get_balance_sheet(self, symbol: str) -> pd.DataFrame:
        """资产负债表（按报告期）。失败 → 透传。"""
        logger.info("正在拉取资产负债表: %s", symbol)
        rows = fetch_balance_sheet(symbol)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    async def get_cashflow_statement(self, symbol: str) -> pd.DataFrame:
        """现金流量表（按报告期）。失败 → 透传。"""
        logger.info("正在拉取现金流量表: %s", symbol)
        rows = fetch_cashflow_statement(symbol)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)

    async def get_income_statement(self, symbol: str) -> pd.DataFrame:
        """利润表（按报告期）。失败 → 透传。"""
        logger.info("正在拉取利润表: %s", symbol)
        rows = fetch_income_statement(symbol)
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows)


def _flatten_indicators(raw_df: pd.DataFrame, *, source: str) -> pd.DataFrame:
    """把 akshare 的宽表（行=日期，列=指标）压成长表（行=日期×指标）。

    输入列（akshare）：日期 / 基本每股收益 / 每股净资产 / ...
    输出列：report_period / indicator_code / indicator_name / value / unit / source
    """
    if raw_df.empty:
        return pd.DataFrame(
            columns=["report_period", "indicator_code", "indicator_name", "value", "unit", "source"]
        )

    # 找日期列
    date_col = None
    for cand in ("日期", "报告期", "REPORT_DATE"):
        if cand in raw_df.columns:
            date_col = cand
            break
    if date_col is None:
        date_col = raw_df.columns[0]

    frames: list[pd.DataFrame] = []
    for col, (code, name, unit) in _INDICATOR_MAP.items():
        if col not in raw_df.columns:
            continue
        sub = pd.DataFrame({
            "report_period": pd.to_datetime(raw_df[date_col], errors="coerce").dt.date,
            "indicator_code": code,
            "indicator_name": name,
            "value": pd.to_numeric(raw_df[col], errors="coerce"),
            "unit": unit,
            "source": source,
        })
        frames.append(sub)

    if not frames:
        return pd.DataFrame(
            columns=["report_period", "indicator_code", "indicator_name", "value", "unit", "source"]
        )
    out = pd.concat(frames, ignore_index=True)
    return out.dropna(subset=["report_period", "value"])


def normalize_indicator_payload(payload: Any) -> pd.DataFrame:
    """统一入口：接受 list[dict] / DataFrame / None，返回标准长表。"""
    if payload is None:
        return pd.DataFrame(
            columns=["report_period", "indicator_code", "indicator_name", "value", "unit", "source"]
        )
    if isinstance(payload, pd.DataFrame):
        return _flatten_indicators(payload, source="external")
    if isinstance(payload, list):
        return _flatten_indicators(pd.DataFrame(payload), source="external")
    raise TypeError(f"unsupported payload type: {type(payload).__name__}")


# ── 财务三表（balance_sheet / cashflow_statement / income_statement）──
#
# 数据源：akshare stock_balance_sheet_by_report_em / stock_cash_flow_sheet_by_report_em /
#         stock_profit_sheet_by_report_em（东财数据中心，按报告期）
#
# 设计：
# - 三个 fetch_* 为模块级函数（便于测试 patch）
# - FinancialReportProvider 提供 3 个 async 包装方法
# - 失败语义：下游 raise → 透传（不静默吞）


def _ensure_akshare():
    """运行时导入 akshare（延迟 import 让单测不依赖真实安装）。"""
    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError(
            "akshare 未安装 — 财务三表接口需要 akshare 提供 stock_balance_sheet_by_report_em 等"
        ) from exc
    return ak


def fetch_balance_sheet(symbol: str) -> list[dict[str, Any]]:
    """拉取资产负债表（按报告期）。失败 → 透传。"""
    ak = _ensure_akshare()
    fn = getattr(ak, "stock_balance_sheet_by_report_em", None)
    if fn is None:
        raise NotImplementedError(
            f"akshare 缺少 stock_balance_sheet_by_report_em 接口"
        )
    df = fn(stock=symbol)
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def fetch_cashflow_statement(symbol: str) -> list[dict[str, Any]]:
    """拉取现金流量表（按报告期）。失败 → 透传。"""
    ak = _ensure_akshare()
    fn = getattr(ak, "stock_cash_flow_sheet_by_report_em", None)
    if fn is None:
        raise NotImplementedError(
            f"akshare 缺少 stock_cash_flow_sheet_by_report_em 接口"
        )
    df = fn(stock=symbol)
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")


def fetch_income_statement(symbol: str) -> list[dict[str, Any]]:
    """拉取利润表（按报告期）。失败 → 透传。"""
    ak = _ensure_akshare()
    fn = getattr(ak, "stock_profit_sheet_by_report_em", None)
    if fn is None:
        raise NotImplementedError(
            f"akshare 缺少 stock_profit_sheet_by_report_em 接口"
        )
    df = fn(stock=symbol)
    if df is None or df.empty:
        return []
    return df.to_dict(orient="records")
