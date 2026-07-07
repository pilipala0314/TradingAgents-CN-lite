"""TacticalScorer — 战术标签 + 市场/板块阶段 + 流动性/拥挤度打分。

不依赖外部 I/O，仅基于已拉取的数据计算。

⚠️ 数据缺失兜底约定：
- `score_liquidity` / `score_crowding` / `score_valuation_percentile_from_pe`
  在输入缺失时会返回 0.5（中位），并打 `logger.debug` 记录。
- 调用方（assembler / agent）应同时读取 `stock_snapshot.missing_fields`
  判断该分是否为"猜的"；不要单凭 0.5 推断"流动性中等"。
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.5
    return max(lo, min(hi, float(x)))


# 知名游资 / 机构（用于 detect_lhb_famous_buyers 龙虎榜席位识别）
FAMOUS_BRANDS: tuple[str, ...] = (
    "东方财富", "国泰海通", "中信证券", "华泰证券", "招商证券",
    "国信证券", "海通证券", "银河证券", "中金公司", "东方证券",
    "财通证券", "宁波桑田路", "欢乐海", "方新侠", "章盟主", "作手新一",
    "炒股养家", "赵老哥", "佛山系", "成都系", "拉萨天团",
)


def build_market_regime_hint(industry_ranking: list[dict[str, Any]]) -> str:
    """用东财行业涨跌排名推断市场阶段（粗略版，仅供个股层做 upstream_alignment 兜底）。

    规则：
    - 上涨行业占比 > 70% → risk_on_expansion
    - 上涨行业占比 30%~70% → risk_on_rotation
    - 上涨行业占比 10%~30% → neutral_mixed
    - 上涨行业占比 < 10% → risk_off_stabilized
    - 全行业下跌 → risk_off_panic
    """
    if not industry_ranking:
        return "neutral_mixed"
    up = sum(1 for r in industry_ranking if float(r.get("change_pct") or 0) > 0)
    down = sum(1 for r in industry_ranking if float(r.get("change_pct") or 0) < 0)
    total = up + down
    if total == 0:
        return "neutral_mixed"
    up_ratio = up / total
    if up_ratio > 0.7:
        return "risk_on_expansion"
    if up_ratio > 0.3:
        return "risk_on_rotation"
    if up_ratio > 0.1:
        return "neutral_mixed"
    if up > 0:
        return "risk_off_stabilized"
    return "risk_off_panic"


def infer_sector_stage(sector_change_pct: float) -> str:
    """用板块当日涨跌幅粗略推断阶段（仅在板块分析缺失时作为兜底）。"""
    if sector_change_pct >= 5.0:
        return "strengthening"
    if sector_change_pct >= 2.0:
        return "confirmed"
    if sector_change_pct >= 0.5:
        return "emerging"
    if sector_change_pct <= -3.0:
        return "decaying"
    if sector_change_pct <= -1.0:
        return "diverging"
    return "watching"


def detect_auction_signal(quote_data: dict[str, Any]) -> str:
    """用开盘价 vs 昨收推断竞价信号。"""
    open_p = quote_data.get("open") or 0
    last_close = quote_data.get("last_close") or 0
    if last_close <= 0:
        return "neutral"
    gap = (open_p - last_close) / last_close * 100
    if gap > 1.5:
        return "strong_open"
    if gap > 0.3:
        return "weak_to_strong"
    if gap < -1.5:
        return "weak_open"
    if gap < -0.3:
        return "strong_to_weak"
    return "neutral"


def detect_tactical_flags(
    kline: list[dict[str, Any]],
    today_quote: dict[str, Any],
) -> dict[str, bool]:
    """基于 K 线 + 当日盘口推断战术标签。"""
    flags = {
        "first_yin_after_streak": False,
        "streak_break": False,
        "reversal_board": False,
        "weak_to_strong": False,
        "a_kill": False,
        "limit_up_to_limit_down": False,
        "limit_down_to_limit_up": False,
        "open_high_drop": False,
        "open_low_reversal": False,
        "laggard_rise": False,
        "rotten_board_reseal": False,
        "position_steal": False,
    }
    if not kline:
        return flags

    change_pct = today_quote.get("change_pct") or 0
    if change_pct >= 9.5:
        flags["weak_to_strong"] = True

    last_5 = kline[-6:-1] if len(kline) >= 6 else kline[:-1]
    prev_limit_up = any(
        (bar["close"] - bar["open"]) / max(bar["open"], 0.01) >= 0.095 for bar in last_5
    )
    if prev_limit_up and change_pct < 5:
        flags["streak_break"] = True
        if change_pct < 0:
            flags["first_yin_after_streak"] = True

    open_p = today_quote.get("open") or 0
    last_close = today_quote.get("last_close") or 0
    high_p = today_quote.get("high") or 0
    today_quote.get("low") or 0
    price = today_quote.get("price") or 0
    if last_close > 0 and high_p > 0 and price > 0:
        if open_p > last_close * 1.005 and price < open_p * 0.97:
            flags["open_high_drop"] = True
        if open_p < last_close * 0.995 and price > open_p * 1.02 and price > last_close:
            flags["open_low_reversal"] = True

    amplitude = today_quote.get("amplitude_pct") or 0
    if amplitude >= 19 and change_pct <= -9:
        flags["limit_up_to_limit_down"] = True
    if amplitude >= 19 and change_pct >= 9:
        flags["limit_down_to_limit_up"] = True
        flags["reversal_board"] = True
        flags["rotten_board_reseal"] = True
    if change_pct <= -9.5:
        flags["a_kill"] = True

    return flags


def score_liquidity(amount_wan: float, mcap_yi: float) -> float:
    """流动性 = 日成交额 / 总市值（归一化到 [0,1]，3% 换手 ≈ 0.9）。

    数据缺失（amount 或 mcap ≤ 0）→ 返回 0.5 并 logger.debug。
    """
    if mcap_yi <= 0 or amount_wan <= 0:
        logger.debug(
            "score_liquidity 兜底: amount_wan=%s mcap_yi=%s → 0.5 (数据缺失)",
            amount_wan, mcap_yi,
        )
        return 0.5
    turnover_rate = (amount_wan * 1e4) / (mcap_yi * 1e8)
    return _clamp(turnover_rate * 30.0)


def score_crowding(turnover_pct: float, vol_ratio: float) -> float:
    """拥挤度 = 换手率 + 量比（归一化）。

    数据缺失（输入 None / NaN）→ 返回 0.5 并 logger.debug。
    """
    if turnover_pct is None or vol_ratio is None:
        logger.debug(
            "score_crowding 兜底: turnover_pct=%s vol_ratio=%s → 0.5 (数据缺失)",
            turnover_pct, vol_ratio,
        )
        return 0.5
    return _clamp((turnover_pct / 10.0) * 0.6 + (vol_ratio / 3.0) * 0.4)


def score_valuation_percentile_from_pe(pe_ttm: float) -> float:
    """用 PE-TTM 经验分段估算估值百分位（无全市场分布时使用）。

    数据缺失（pe_ttm 无效）→ 返回 0.5 并 logger.debug。
    """
    if not pe_ttm or pe_ttm <= 0:
        logger.debug("score_valuation_percentile_from_pe 兜底: pe_ttm=%s → 0.5 (数据缺失)", pe_ttm)
        return 0.5
    if pe_ttm < 0:
        return 0.95
    if pe_ttm < 15:
        return 0.2
    if pe_ttm < 30:
        return 0.4
    if pe_ttm < 60:
        return 0.6
    if pe_ttm < 100:
        return 0.8
    return 0.95


def detect_lhb_famous_buyers(
    lhb: dict[str, Any] | None,
) -> tuple[bool, bool]:
    """龙虎榜席位名匹配知名游资 / 机构专用。

    返回 (famous_buy_flag, famous_sell_flag)。
    """
    if not lhb:
        return False, False
    famous_buy = any(
        any(brand in (seat.get("name") or "") for brand in FAMOUS_BRANDS)
        for seat in (lhb.get("seats") or {}).get("buy", []) or []
    )
    famous_sell = any(
        any(brand in (seat.get("name") or "") for brand in FAMOUS_BRANDS)
        for seat in (lhb.get("seats") or {}).get("sell", []) or []
    )
    return famous_buy, famous_sell
