"""StockLiveContext — 个股实景上下文的统一入口（Facade）。

按以下顺序执行：
1. 通过 `DataRouter` 并发拉取 10 个端点（akshare + a_stock_data 共 28 method，
   享受 cache / stats / timeout / strict）
2. `assembler.assemble(...)` 合成 stock_snapshot
3. 推断 market_regime / sector_stage
4. 返回聚合 dict（含 stock_snapshot + 上位上下文 + source_meta + raw）

异步化：
- 内部端点仍是 sync（mootdx / requests / ak），由 provider 内部 `asyncio.to_thread` 包成 awaitable
- facade 用 `asyncio.gather` 并发调度 10 端点
- DataRouter 额外用 `asyncio.wait_for` 包每个端点，timeout 统一可配

配置化（来自 settings）：
- `ROUTE_DEFAULT_TIMEOUT`  单端点超时（默认 10s）
- `ROUTE_DEFAULT_STRICT`   strict 模式（默认 False — 全部失败才抛）
- `ROUTE_CACHE_TTL_SEC`    TTL 缓存（默认 30s）

source_meta 字段命名约定（与 DataRouter 健康度对齐）：
- key 直接用 method 名（如 `get_tencent_quote` / `get_kline`），
  方便与 `GET /health` 的 `router.endpoints[].method` 直接对账。
- 旧 facade 字符串键（`tencent` / `mootdx_kline` 等）已废弃，调用方请改用 method 名。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from data_pipeline.data_router import DataFetchError, DataRouter

from ._shared import normalize_ticker
from .assembler import assemble, pick_sector_stage
from .scorer import build_market_regime_hint

logger = logging.getLogger(__name__)


# ── 单例 router ────────────────────────────────────────────────────────
_router: DataRouter | None = None


def get_router() -> DataRouter:
    """获取 DataRouter 单例（懒加载）。"""
    global _router
    if _router is None:
        _router = DataRouter()
    return _router


def set_router(router: DataRouter) -> None:
    """覆盖默认 router（测试 / 注入自定义 provider 用）。"""
    global _router
    _router = router


# ── source_meta 状态判定（统一在 facade 内做） ─────────────────────────

def _endpoint_is_ok(method: str, result: Any) -> bool:
    """判定端点返回是否成功（非空 + 业务上有意义）。

    收编原 `_is_empty_result` 的所有判断逻辑到这里，source_meta 构造时复用。
    """
    if result is None:
        return False
    # list / tuple：非空就算 ok
    if isinstance(result, (list, tuple)):
        return len(result) > 0
    # dict：分类型判断
    if isinstance(result, dict):
        # get_concept_blocks: total > 0 才有意义
        if "boards" in result and "total" in result:
            return result.get("total", 0) > 0
        # get_dragon_tiger: records 或 seats 任一非空算 ok
        if "records" in result or "seats" in result:
            return bool(result.get("records") or result.get("seats"))
        # 其他 dict：非空就算 ok
        return bool(result)
    # 标量值：非空（falsy 值如 0/"" 也算 ok，调用方自己判定）
    return True


async def _route_one(
    router: DataRouter,
    method: str,
    *args: Any,
    timeout: float | None = None,
    strict: bool = False,
    use_cache: bool = True,
    **kwargs: Any,
) -> Any:
    """包装 router.route，单端点失败按 strict 决定是否抛错。

    注意：timeout / strict / use_cache 是 facade 控制参数（透传给 router），
    不会被透传给 provider 函数本身。
    """
    try:
        return await router.route(
            method,
            *args,
            timeout=timeout,
            strict=strict,
            use_cache=use_cache,
            **kwargs,
        )
    except DataFetchError as exc:
        logger.warning("端点 %s 失败: %s", method, exc.last_error)
        if strict:
            raise
        # 非 strict：返回 None / 空值，由调用方 source_meta 标记 failed
        return None


# ── facade 拉的 10 个端点（按 method 声明顺序固定） ─────────────────
# 9 个走 a_stock_data provider，1 个 (get_stock_news) 先 akshare 后 a_stock_data 兜底。
_FACADE_ENDPOINTS: list[str] = [
    "get_tencent_quote",        # a_stock_data: 腾讯实时估值
    "get_kline",                # a_stock_data: mootdx 日 K
    "get_quote_5level",         # a_stock_data: mootdx 五档盘口
    "get_concept_blocks",       # a_stock_data: 个股板块归属
    "get_industry_ranking",     # a_stock_data: 行业涨跌排名
    "get_fund_flow_minute",     # a_stock_data: 分钟级资金流
    "get_dragon_tiger",         # a_stock_data: 龙虎榜聚合
    "get_stock_info",           # a_stock_data: 个股基本面
    "get_finance_snapshot",     # a_stock_data: 季报快照
    "get_stock_news",           # akshare (优先) + a_stock_data 兜底
]


async def stock_live_context(
    symbol: str,
    business_date: str | None = None,
    *,
    strict: bool = False,
    timeout: float | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """拉取个股实景上下文：合成 stock_snapshot + market_regime + sector_stage。

    任何 provider 端点失败都被 try/except 吞掉（除非 strict=True），
    snapshot 字段缺失会保留为 None；source_meta 记录每个端点的状态（ok / failed）。

    异步化：N 个端点用 `asyncio.gather` 并发（provider 内部 asyncio.to_thread 包 sync），
    单次请求理论时长 ≈ 最慢端点耗时（1~2s），不再线性叠加。

    Args:
        symbol:         6 位股票代码（支持 sh688017 / 688017.SH / 688017）
        business_date:  YYYY-MM-DD，默认今日
        strict:         True=任一端点失败立即抛错；False=全部失败才抛（默认）
        timeout:        单端点超时（秒），默认走 settings.ROUTE_DEFAULT_TIMEOUT
        use_cache:      是否使用 DataRouter 的 TTL 缓存（默认 True）

    Returns:
        dict with keys:
            stock_snapshot     — 合成后的快照（symbol / current_price / ...）
            market_regime      — 上位市场态
            sector_stage       — 上位板块态
            sector_change_pct  — 板块涨跌
            source_meta        — {method: 'ok'|'failed'}  （key 与 router.health() 对齐）
            raw                — 原始 10 端点返回值（debug 用）
    """
    code = normalize_ticker(symbol)
    business_date = business_date or datetime.now().strftime("%Y-%m-%d")
    router = get_router()

    # 1) 并发拉取 10 个端点（统一通过 DataRouter，享受 cache / stats / timeout / strict）
    results: list[Any] = await asyncio.gather(
        _route_one(router, "get_tencent_quote", [code], timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_kline", code, 9, 60, timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_quote_5level", code, timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_concept_blocks", code, timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_industry_ranking", 90, timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_fund_flow_minute", code, timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_dragon_tiger", code, None, 30, timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_stock_info", code, timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_finance_snapshot", code, timeout=timeout, strict=strict, use_cache=use_cache),
        _route_one(router, "get_stock_news", code, 20, timeout=timeout, strict=strict, use_cache=use_cache),
    )
    # 拆结果（顺序与 _FACADE_ENDPOINTS 一一对应）
    (tencent, kline, _quotes, blocks, industry_ranking, flow, lhb, info, fin, news_list) = (
        (r if r is not None else default)
        for r, default in zip(
            results,
            [
                {},                  # tencent         → dict
                [],                  # kline           → list
                None,                # quotes          → dict 或 None
                {"boards": [], "concept_tags": []},  # blocks → 失败时回退 dict
                [],                  # industry_ranking → list
                [],                  # flow            → list
                {},                  # lhb             → dict
                {},                  # info            → dict
                None,                # fin             → dict 或 None
                [],                  # news_list       → list
            ], strict=False,
        )
    )

    # 2) source_meta：key 用 method 名（与 router.health() 直接对齐）
    source_meta: dict[str, str] = {
        method: ("ok" if _endpoint_is_ok(method, results[idx]) else "failed")
        for idx, method in enumerate(_FACADE_ENDPOINTS)
    }

    # 3) 合成 stock_snapshot
    tencent_one = tencent.get(code, {}) if isinstance(tencent, dict) else {}
    snapshot, sector_change_pct = assemble(
        code=code,
        business_date=business_date,
        tencent_one=tencent_one,
        kline=kline or [],
        blocks=blocks.get("boards", []) if isinstance(blocks, dict) else [],
        flow=flow or [],
        lhb=lhb or {},
        info=info or {},
        fin=fin,
        news_list=news_list or [],
    )
    snapshot["data_freshness"]["source_meta"] = source_meta

    # 4) 上位上下文
    market_regime = build_market_regime_hint(industry_ranking or [])
    sector_stage = pick_sector_stage(sector_change_pct) if sector_change_pct else "watching"

    # 5) raw 段（安全 slice，None 不崩）
    return {
        "stock_snapshot": snapshot,
        "market_regime": market_regime,
        "sector_stage": sector_stage,
        "sector_change_pct": sector_change_pct,
        "source_meta": source_meta,
        "raw": {
            "tencent_quote": tencent_one,
            "concept_blocks": blocks or {},
            "industry_ranking_top10": (industry_ranking or [])[:10],
            "fund_flow_minute_tail": (flow or [])[-3:],
            "dragon_tiger": lhb or {},
            "stock_info": info or {},
            "finance_snapshot": fin,
            "stock_news": (news_list or [])[:10],
            "kline_tail": (kline or [])[-10:],
        },
    }
