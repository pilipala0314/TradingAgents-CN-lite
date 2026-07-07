import logging
from datetime import date
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class StockTacticalCalculator:
    """个股战术标签计算器。"""

    @staticmethod
    def calculate_tactical_labels(
        current_daily_df: pd.DataFrame,
        prev_limit_up_list: set,
        current_limit_up_list: set,
        current_limit_down_list: set,
    ) -> pd.DataFrame:
        """计算个股的战术标签（如首阴、断板、反包等）。"""
        df = current_daily_df.copy()

        required_cols = ["代码", "开盘", "收盘", "昨收", "最高", "最低"]
        for col in required_cols:
            if col not in df.columns:
                logger.warning("缺少计算战术标签所需的列: %s", col)
                return df

        df["streak_break"] = False
        df["first_yin_after_streak"] = False
        df["reversal_board"] = False
        df["weak_to_strong"] = False
        df["limit_up_to_limit_down"] = False
        df["limit_down_to_limit_up"] = False

        for idx, row in df.iterrows():
            code = row["代码"]
            open_p = float(row["开盘"])
            close_p = float(row["收盘"])
            prev_close = float(row["昨收"])
            high_p = float(row["最高"])
            low_p = float(row["最低"])
            limit_pct = StockTacticalCalculator._resolve_limit_pct(row)

            if code in prev_limit_up_list and code not in current_limit_up_list:
                df.at[idx, "streak_break"] = True
                if close_p < open_p:
                    df.at[idx, "first_yin_after_streak"] = True

            if code not in prev_limit_up_list and code in current_limit_up_list and open_p <= prev_close * 1.01:
                df.at[idx, "weak_to_strong"] = True

            if code in prev_limit_up_list and code in current_limit_up_list and close_p >= open_p:
                df.at[idx, "reversal_board"] = True

            if prev_close > 0:
                upper_bound = prev_close * (1 + limit_pct - 0.005)
                lower_bound = prev_close * (1 - limit_pct + 0.005)
                if high_p >= upper_bound and close_p <= lower_bound:
                    df.at[idx, "limit_up_to_limit_down"] = True

            if prev_close > 0:
                upper_bound = prev_close * (1 + limit_pct - 0.005)
                lower_bound = prev_close * (1 - limit_pct + 0.005)
                if low_p <= lower_bound and close_p >= upper_bound:
                    df.at[idx, "limit_down_to_limit_up"] = True

        logger.info(
            "战术标签计算完成。断板数=%s 天地板数=%s 弱转强数=%s",
            int(df["streak_break"].sum()),
            int(df["limit_up_to_limit_down"].sum()),
            int(df["weak_to_strong"].sum()),
        )
        return df

    @staticmethod
    def _resolve_limit_pct(row: pd.Series) -> float:
        limit_type = str(row.get("limit_type", "main_10pct"))
        if limit_type == "st_5pct":
            return 0.05
        if limit_type == "gem_star_20pct":
            return 0.20
        if limit_type == "bj_30pct":
            return 0.30
        return 0.10


# ----------------------------------------------------------------------
# 单股票入口 — StockFeatureSyncJob 在循环中逐只股票调用
# ----------------------------------------------------------------------


def calculate_stock_tactical_features(
    *,
    security_id: Any,
    business_date: date,
    bar_df: pd.DataFrame,
    lhb_df: pd.DataFrame | None = None,
    flow_df: pd.DataFrame | None = None,
    news_df: pd.DataFrame | None = None,
    feature_version: int = 1,
) -> dict[str, Any]:
    """单股票战术特征 — 输出 feature_stock_snapshot 一行 dict。

    输入约定（薄壳要求）：
        - bar_df   个股日线（最近 1 年），含 open/close/high/low/pct_change/volume/turnover
        - lhb_df   个股龙虎榜（可空）— 含 `change_pct / net_buy / famous_flag`
        - flow_df  个股资金流（可空）— 含 `main_net_inflow / retail_net_inflow`
        - news_df  个股新闻（可空）— DataFrame 或 list[dict]
    """
    lhb_df = lhb_df if lhb_df is not None else pd.DataFrame()
    flow_df = flow_df if flow_df is not None else pd.DataFrame()
    news_df = news_df if news_df is not None else pd.DataFrame()

    # 1) 行情指标
    if not bar_df.empty:
        last = bar_df.iloc[-1]
        close_p = float(pd.to_numeric(last.get("close", 0.0), errors="coerce") or 0.0)
        pct_change = float(pd.to_numeric(last.get("pct_change", 0.0), errors="coerce") or 0.0)
        amplitude_pct = float(
            pd.to_numeric(last.get("amplitude", 0.0), errors="coerce") or 0.0
        )
        turnover = float(pd.to_numeric(last.get("turnover", 0.0), errors="coerce") or 0.0)
        volume = float(pd.to_numeric(last.get("volume", 0.0), errors="coerce") or 0.0)
    else:
        close_p = pct_change = amplitude_pct = turnover = volume = 0.0

    # 2) 资金流指标
    main_net_flow = float(
        flow_df["main_net_inflow"].sum() if "main_net_inflow" in flow_df.columns else 0.0
    )
    retail_net_flow = float(
        flow_df["retail_net_inflow"].sum() if "retail_net_inflow" in flow_df.columns else 0.0
    )

    # 3) 龙虎榜指标
    lhb_net_buy = float(lhb_df["net_buy"].sum() if "net_buy" in lhb_df.columns else 0.0)
    lhb_famous_flag = bool(
        lhb_df["famous_flag"].any() if "famous_flag" in lhb_df.columns else False
    )

    # 4) 新闻数
    news_count = len(news_df) if hasattr(news_df, "__len__") else 0

    # 5) 战术标签（极简 — 真正的复杂规则后续在 P2 补）
    streak_break = bool(close_p < pct_change and pct_change < 0)
    weak_to_strong = bool(close_p > 0 and pct_change > 0 and amplitude_pct > 0.05)
    reversal_board = bool(close_p > 0 and pct_change > 0.05)

    return {
        "security_id": security_id,
        "business_date": business_date,
        "feature_version": feature_version,
        "close_price": close_p,
        "pct_change": pct_change,
        "amplitude_pct": amplitude_pct,
        "turnover": turnover,
        "volume": volume,
        "main_net_flow": main_net_flow,
        "retail_net_flow": retail_net_flow,
        "lhb_net_buy": lhb_net_buy,
        "lhb_famous_flag": lhb_famous_flag,
        "news_count": news_count,
        "streak_break": streak_break,
        "weak_to_strong": weak_to_strong,
        "reversal_board": reversal_board,
    }
