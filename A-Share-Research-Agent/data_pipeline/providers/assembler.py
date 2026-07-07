"""StockSnapshotAssembler — 把多个 provider 返回值合成 StockAnalyst 所需的 stock_snapshot。

不调任何外部 I/O，纯数据拼装 + 字段映射。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

from data_pipeline.providers.scorer import (  # noqa: E402
    _clamp,
    detect_auction_signal,
    detect_lhb_famous_buyers,
    detect_tactical_flags,
    infer_sector_stage,
    score_crowding,
    score_liquidity,
    score_valuation_percentile_from_pe,
)


def _select_industry_block(
    blocks: list[dict[str, Any]],
    industry_name: str,
) -> tuple[str, float]:
    """从 slist boards 中选行业归属板块（优先行业名匹配）。"""
    if not blocks:
        return "", 0.0
    for b in blocks:
        if b.get("name") == industry_name:
            return b.get("code") or b.get("name") or "", _safe_float(b.get("change_pct"))
    first = blocks[0]
    return first.get("code") or first.get("name") or "", _safe_float(first.get("change_pct"))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _detect_is_st(name: str) -> bool:
    return bool(name) and ("ST" in name or "退" in name)


def _detect_limit_type(code: str, name: str, industry_name: str) -> str:
    if _detect_is_st(name):
        return "st_5pct"
    if any(t in (industry_name or "") for t in ("创业板", "科创")):
        return "chinext_20pct"
    if code.startswith("8"):
        return "bj_30pct"
    return "main_10pct"


def assemble(
    *,
    code: str,
    business_date: str,
    tencent_one: dict[str, Any],
    kline: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    flow: list[dict[str, Any]],
    lhb: dict[str, Any] | None,
    info: dict[str, Any],
    fin: dict[str, Any] | None,
    news_list: list[dict[str, Any]],
) -> tuple[dict[str, Any], float]:
    """合成 stock_snapshot。返回 (snapshot, sector_change_pct)。"""
    pe_ttm = tencent_one.get("pe_ttm") or 0
    pb = tencent_one.get("pb") or 0
    turnover_pct = tencent_one.get("turnover_pct") or 0
    vol_ratio = tencent_one.get("vol_ratio") or 0
    amount_wan = tencent_one.get("amount_wan") or 0
    mcap_yi = tencent_one.get("mcap_yi") or 0
    float_mcap_yi = tencent_one.get("float_mcap_yi") or 0
    price = tencent_one.get("price") or 0

    industry_name = info.get("industry", "") if info else ""
    sector_id, sector_change_pct = _select_industry_block(blocks, industry_name)

    concept_tags = [b.get("name", "") for b in (blocks or [])]
    flags = detect_tactical_flags(kline or [], tencent_one)
    auction_signal = detect_auction_signal(tencent_one)

    main_net_total = sum((r.get("main_net") or 0) for r in (flow or []))
    main_net_turnover_ratio = 0.0
    if amount_wan > 0 and flow:
        main_net_turnover_ratio = main_net_total / (amount_wan * 1e4)

    lhb = lhb or {}
    lhb_inst = lhb.get("institution", {}) or {}
    lhb_net_buy_wan = lhb_inst.get("net_amt", 0.0) or 0.0
    lhb_total_buy = lhb_inst.get("buy_amt", 0.0) or 0.0
    lhb_total_sell = lhb_inst.get("sell_amt", 0.0) or 0.0
    lhb_total = max(lhb_total_buy + lhb_total_sell, 1e-6)
    lhb_inst_buy_ratio = lhb_total_buy / lhb_total
    lhb_inst_sell_ratio = lhb_total_sell / lhb_total
    lhb_listed = bool(lhb.get("records"))

    famous_buy, famous_sell = detect_lhb_famous_buyers(lhb)

    stock_name = (info.get("name") if info else "") or tencent_one.get("name") or ""
    is_st = _detect_is_st(stock_name)
    abnormal_halt_risk = price == 0 and bool(stock_name)
    limit_type = _detect_limit_type(code, stock_name, industry_name)

    valuation_percentile = score_valuation_percentile_from_pe(pe_ttm)
    crowding_score = score_crowding(turnover_pct, vol_ratio)
    liquidity_score = score_liquidity(amount_wan, mcap_yi)

    announcement_risk_score = 0.2
    if info and info.get("list_date"):
        try:
            days_listed = (
                datetime.strptime(business_date, "%Y-%m-%d")
                - datetime.strptime(str(info["list_date"])[:8], "%Y%m%d")
            ).days
            if days_listed < 60:
                announcement_risk_score = 0.6
        except (ValueError, TypeError) as exc:
            logger.debug("list_date 解析失败, business_date=%s info=%s: %s", business_date, info.get("list_date"), exc)

    concept_purity_score = _clamp(len(concept_tags) / 6.0)

    support_level = tencent_one.get("low") or (price * 0.97 if price else 0)
    resistance_level = tencent_one.get("high") or (price * 1.05 if price else 0)
    stop_loss_hint = tencent_one.get("limit_down") or (price * 0.97 if price else 0)

    snapshot = {
        "symbol": code,
        "security_id": code,
        "name": stock_name,
        "industry": industry_name,
        "sector_id": sector_id,
        "sector_name": industry_name,
        "concept_tags": concept_tags,
        "current_price": price,
        "prev_close": tencent_one.get("last_close") or 0,
        "open_price": tencent_one.get("open") or 0,
        "high_price": tencent_one.get("high") or 0,
        "low_price": tencent_one.get("low") or 0,
        "change_pct": tencent_one.get("change_pct") or 0,
        "turnover_pct": turnover_pct,
        "volume_ratio": vol_ratio,
        "amount": (amount_wan or 0) * 1e4,
        "pe_ttm": pe_ttm,
        "pe_static": tencent_one.get("pe_static") or 0,
        "pb": pb,
        "amplitude_pct": tencent_one.get("amplitude_pct") or 0,
        "mcap_yi": mcap_yi,
        "float_mcap_yi": float_mcap_yi,
        "valuation_percentile": valuation_percentile,
        "crowding_score": crowding_score,
        "liquidity_score": liquidity_score,
        "announcement_risk_score": announcement_risk_score,
        "concept_purity_score": concept_purity_score,
        "auction_signal": auction_signal,
        "open_gap_pct": (
            ((tencent_one.get("open") or 0) - (tencent_one.get("last_close") or 0))
            / max(tencent_one.get("last_close") or 0, 0.01)
            * 100
            if tencent_one.get("last_close")
            else 0
        ),
        "open_direction": "up" if (tencent_one.get("open") or 0) > (tencent_one.get("last_close") or 0) else "down",
        "open_high_drop": flags["open_high_drop"],
        "open_low_reversal": flags["open_low_reversal"],
        "first_yin_after_streak": flags["first_yin_after_streak"],
        "streak_break": flags["streak_break"],
        "reversal_board": flags["reversal_board"],
        "weak_to_strong": flags["weak_to_strong"],
        "a_kill": flags["a_kill"],
        "limit_up_to_limit_down": flags["limit_up_to_limit_down"],
        "limit_down_to_limit_up": flags["limit_down_to_limit_up"],
        "rotten_board_reseal": flags["rotten_board_reseal"],
        "laggard_rise": flags["laggard_rise"],
        "position_steal": flags["position_steal"],
        "lhb_listed": lhb_listed,
        "lhb_net_buy": lhb_net_buy_wan * 1e4,
        "lhb_inst_buy_ratio": lhb_inst_buy_ratio,
        "lhb_inst_sell_ratio": lhb_inst_sell_ratio,
        "lhb_famous_buy_flag": famous_buy,
        "lhb_famous_sell_flag": famous_sell,
        "limit_type": limit_type,
        "is_st": is_st,
        "abnormal_halt_risk": abnormal_halt_risk,
        "support_level": round(support_level, 2) if support_level else 0.0,
        "resistance_level": round(resistance_level, 2) if resistance_level else 0.0,
        "stop_loss_hint": round(stop_loss_hint, 2) if stop_loss_hint else 0.0,
        "fundamentals_snapshot": {
            "industry": industry_name,
            "list_date": info.get("list_date", ""),
            "total_shares": info.get("total_shares", 0),
            "float_shares": info.get("float_shares", 0),
            "mcap": info.get("mcap", 0),
            "float_mcap": info.get("float_mcap", 0),
            "eps": (fin or {}).get("eps"),
            "roe": (fin or {}).get("roe"),
            "bvps": (fin or {}).get("bvps"),
            "profit": (fin or {}).get("profit"),
            "income": (fin or {}).get("income"),
        },
        "capital_snapshot": {
            "main_net_total_yuan": main_net_total,
            "main_net_turnover_ratio": main_net_turnover_ratio,
            "lhb_records_count": len(lhb.get("records", []) or []),
            "lhb_institution_net_yuan": lhb_net_buy_wan * 1e4,
        },
        "news_snapshot": {
            "headlines": [
                {
                    "title": n.get("title", ""),
                    "show_time": n.get("show_time", ""),
                    "media_name": n.get("media_name", ""),
                }
                for n in (news_list or [])[:5]
            ],
            "count": len(news_list or []),
        },
        "data_freshness": {
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "business_date": business_date,
        },
    }
    return snapshot, sector_change_pct


def pick_sector_stage(sector_change_pct: float) -> str:
    """从板块涨跌幅推断阶段。"""
    return infer_sector_stage(sector_change_pct)
