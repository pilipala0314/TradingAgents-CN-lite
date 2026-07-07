"""facade_normalizer.py — Facade 层 DataFrame → list[dict] 归一化。

## 设计动机

AkshareProvider 的 28 个端点大多返回 `pd.DataFrame`（ETL 友好），
但 Facade 层和 Agent 层需要 `list[dict]`（避免 DataFrame 的 `ambiguous truth` 报错）。

这个模块在 facade 边界做一次归一化，业务层完全无感。

## 用法

```python
from data_pipeline.facade_normalizer import normalize_response

result = await router.route("get_stock_list")
result = normalize_response("get_stock_list", result)
# result 现在保证是 list[dict]
```

## 失败语义

- DataFrame 空 → 返回空 list（不抛错，调用方自己判定）
- DataFrame 字段含 datetime / NaN → 统一转 str / None
- list[dict] 输入 → 直接返回（幂等）
- dict / None 输入 → 直接返回（按 method 表查不到的端点保持原样）
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── 方法签名表 ──────────────────────────────────────────────────────
#
# 声明哪些 method 应该返回 list[dict]（akshare 风格的 DataFrame 端点）。
# 维护这个表，未来加新端点时只追加即可。
#
# True = 端点应该返回 list[dict]（归一化时把 DataFrame 转 list[dict]）

EXPECT_LIST_OF_DICT_METHODS: set[str] = {
    # 证券主数据
    "get_stock_list",
    "get_stock_fundamentals",
    # 行情（akshare 返回 DataFrame）
    "get_daily_bars",
    "get_minute_bars",
    # 资金流
    "get_market_fund_flow",
    "get_northbound_flow",
    "get_individual_fund_flow",
    # 涨停生态
    "get_limit_up_pool",
    "get_limit_down_pool",
    "get_bomb_board_pool",
    # 龙虎榜
    "get_dragon_tiger_list",
    "get_stock_lhb",
    # 板块
    "get_concept_board_list",
    "get_industry_board_list",
    "get_concept_board_components",
    "get_industry_board_components",
    # 板块数据
    "get_sector_recent_bars",
    "get_sector_fund_flow",
    "get_sector_leaders",
    # 财报
    "get_balance_sheet",
    "get_cashflow_statement",
    "get_income_statement",
    # NOTE: get_insider_transactions 已 raise NotImplementedError（A 股无 SEC 式披露），
    # 移出 EXPECT 表，避免 normalize_response 把 raise 误处理。
    # P0 补强
    "get_earnings_forecast",
    "get_analyst_consensus",
    "get_research_reports",
    "get_regulatory_penalties",
    # 高级新闻/事件
    "get_global_macro_news",
    "get_global_news_aggregated",
    "get_sector_event_signals",
    # 宏观
    "get_macro_pmi",
    "get_macro_cpi",
    "get_macro_m2",
    "get_macro_bond_yield",
    # 研报 RAG（list[dict] 形态）
    "get_research_chunks",
    # 公告层（巨潮 P0）
    "get_cninfo_announcements",
}


# ── 核心归一化函数 ──────────────────────────────────────────────

def normalize_response(
    method: str,
    result: Any,
    *,
    normalize_set: set[str] | None = None,
) -> Any:
    """根据 method 表归一化 provider 返回值。

    Args:
        method:        DataRouter method 名
        result:        provider 返回的任意值
        normalize_set: 强制归一化的 method 集合（None = 用 EXPECT_LIST_OF_DICT_METHODS）

    Returns:
        - DataFrame → list[dict]（字段名 → str 值）
        - list[dict] → 原样返回（幂等，不重新包装）
        - dict → 原样返回
        - None → 空 list
        - 其他类型 → 原样返回
    """
    target_set = normalize_set if normalize_set is not None else EXPECT_LIST_OF_DICT_METHODS
    if method not in target_set:
        return result
    if result is None:
        return []
    if isinstance(result, pd.DataFrame):
        return dataframe_to_list_of_dict(result)
    if isinstance(result, (list, tuple)):
        # list 内部有 DataFrame 也展开（不重新包装纯 list 项）
        out: list[Any] = []
        changed = False
        for item in result:
            if isinstance(item, pd.DataFrame):
                out.extend(dataframe_to_list_of_dict(item))
                changed = True
            else:
                out.append(item)
        return out if changed else result
    return result


def dataframe_to_list_of_dict(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame → list[dict]（优雅处理 NaN / datetime / 各种类型）。"""
    if df is None or len(df) == 0:
        return []
    try:
        # 一次性把 NaN → None，datetime → str
        df_clean = df.copy()
        # 列名统一 str
        df_clean.columns = [str(c) for c in df_clean.columns]
        # 替换 NaN / NaT
        df_clean = df_clean.replace({np.nan: None, pd.NaT: None})
        # 记录每列的 dtype（用于正确还原 int 列）
        int_cols = {col for col in df_clean.columns if pd.api.types.is_integer_dtype(df_clean[col])}
        # 逐行转 dict
        rows: list[dict[str, Any]] = []
        for _, row in df_clean.iterrows():
            row_dict: dict[str, Any] = {}
            for col in df_clean.columns:
                val = row[col]
                # 整数列避免被转成 float
                if col in int_cols and val is not None and not (isinstance(val, float) and np.isnan(val)):
                    val = int(val)
                row_dict[col] = _clean_value(val)
            rows.append(row_dict)
        return rows
    except Exception as exc:
        logger.warning("DataFrame → list[dict] 转换失败: %s", exc)
        return []


def _clean_value(val: Any) -> Any:
    """单值清洗：NaN → None，datetime → str，原生类型直通。"""
    if val is None:
        return None
    if isinstance(val, float) and np.isnan(val):
        return None
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        f = float(val)
        return None if np.isnan(f) else f
    if isinstance(val, (datetime, date, pd.Timestamp)):
        return val.isoformat()
    if isinstance(val, np.bool_):
        return bool(val)
    return val


# ── 便捷封装：给 Facade 用的批量归一化 ──────────────────────────

def normalize_facade_results(
    results: dict[str, Any],
    *,
    methods_to_normalize: set[str] | None = None,
) -> dict[str, Any]:
    """对 facade 的多端点结果批量归一化。

    Args:
        results:              {method: result} 字典
        methods_to_normalize: 指定要归一化的 method 子集（None = 用 EXPECT_LIST_OF_DICT_METHODS）

    Returns:
        同结构 dict，DataFrame → list[dict]
    """
    target = methods_to_normalize if methods_to_normalize is not None else EXPECT_LIST_OF_DICT_METHODS
    out: dict[str, Any] = {}
    for method, val in results.items():
        normalized = normalize_response(method, val, normalize_set=target)
        out[method] = normalized
    return out


# ── 优雅降级：批量读库专用 ─────────────────────────────────────

def safe_dataframe_records(df: pd.DataFrame, *, limit: int | None = None) -> list[dict[str, Any]]:
    """保守版 DataFrame → list[dict]（带 limit），给 ETL 入库用。

    失败不抛错，返回空 list。
    """
    try:
        if df is None or len(df) == 0:
            return []
        if limit is not None and limit > 0:
            df = df.head(limit)
        return dataframe_to_list_of_dict(df)
    except Exception as exc:
        logger.warning("safe_dataframe_records 失败: %s", exc)
        return []
