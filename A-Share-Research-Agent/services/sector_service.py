"""P1-T9 板块桑基图服务 — 行业 → 准入/拦截个股的资金/评分流。

数据来源：
- 准入 / 拦截 signals（services/signal_persister.fetch_signals_latest）
- 行业映射（models.dimensions.MapSecuritySector + DimSector）

输出结构（chartjs-chart-sankey 兼容）：
{
  "nodes": [
    {"id": "sw_801010", "name": "农林牧渔", "kind": "sector"},
    {"id": "stock_600519", "name": "贵州茅台", "kind": "stock"},
    ...
  ],
  "links": [
    {"source": "sw_801010", "target": "stock_600519", "value": 0.85, "kind": "approved"},
    ...
  ]
}

无数据时显式返空结构，不静默失败。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def get_sector_flow(
    session: Any,
    *,
    business_date: Any | None = None,
    limit_per_type: int = 50,
    max_links: int = 60,
) -> dict[str, Any]:
    """构造 sector → stock 桑基数据。

    Args:
        session:  AsyncSession
        business_date: 业务日期（None = 最新）
        limit_per_type: 拉取 signals 时每类上限
        max_links: 桑基 link 数量上限（防过密）

    Returns:
        {nodes: [...], links: [...], counts: {...}, reason: str}
    """
    from services.signal_persister import fetch_signals_latest

    try:
        grouped = await fetch_signals_latest(
            session, business_date=business_date, limit_per_type=limit_per_type,
        )
    except Exception as exc:
        logger.error("[sector_service] fetch_signals_latest 失败: %s", exc, exc_info=True)
        return {
            "nodes": [], "links": [],
            "counts": {"sectors": 0, "stocks": 0, "links": 0},
            "reason": f"fetch_signals_latest 失败: {type(exc).__name__}: {exc}",
        }

    approved = grouped.get("approved", []) or []
    blocked = grouped.get("blocked", []) or []
    if not approved and not blocked:
        return {
            "nodes": [], "links": [],
            "counts": {"sectors": 0, "stocks": 0, "links": 0},
            "reason": "当日无 approved/blocked signals",
        }

    # 收集 entity_id → entity_id (symbol)
    stock_ids = []
    for sig in approved + blocked:
        eid = sig.get("entity_id", "")
        if eid and eid not in stock_ids:
            stock_ids.append(eid)
        if len(stock_ids) >= 50:
            break  # 控制 sector 查询规模

    # 拉 sector 映射
    sector_map: dict[str, list[str]] = {}  # symbol → [sector_code, sector_name]
    if stock_ids:
        try:
            from sqlalchemy import select

            from models.dimensions import DimSector, DimSecurity, MapSecuritySector

            stmt = (
                select(
                    DimSecurity.symbol,
                    DimSector.sector_code,
                    DimSector.sector_name,
                )
                .join(MapSecuritySector, MapSecuritySector.security_id == DimSecurity.security_id)
                .join(DimSector, MapSecuritySector.sector_id == DimSector.sector_id)
                .where(DimSecurity.symbol.in_(stock_ids))
            )
            res = await session.execute(stmt)
            for sym, code, name in res.all():
                sector_map[sym] = [code or "unknown", name or code or "未分类"]
        except Exception as exc:
            logger.warning("[sector_service] sector 映射查询失败: %s", exc)

    # 构造 nodes + links
    nodes_map: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    def _add_node(node_id: str, name: str, kind: str) -> None:
        if node_id not in nodes_map:
            nodes_map[node_id] = {"id": node_id, "name": name, "kind": kind}

    for sig in approved:
        sym = sig.get("entity_id", "")
        conf = float(sig.get("confidence", 0.0) or 0.0)
        sec = sector_map.get(sym)
        if sec:
            sec_id, sec_name = sec
            _add_node(sec_id, sec_name, "sector")
            _add_node(f"stock_{sym}", sym, "stock")
            links.append({
                "source": sec_id, "target": f"stock_{sym}",
                "value": round(max(conf, 0.05), 4), "kind": "approved",
            })

    for sig in blocked:
        sym = sig.get("entity_id", "")
        conf = float(sig.get("confidence", 0.0) or 0.0)
        sec = sector_map.get(sym)
        if sec:
            sec_id, sec_name = sec
            _add_node(sec_id, sec_name, "sector")
            _add_node(f"stock_{sym}", sym, "stock")
            links.append({
                "source": sec_id, "target": f"stock_{sym}",
                "value": round(max(conf, 0.05), 4), "kind": "blocked",
            })

    # 未映射到 sector 的 stock 兜底（孤点）
    for sig in approved + blocked:
        sym = sig.get("entity_id", "")
        if sym and sym not in sector_map:
            _add_node(f"stock_{sym}", sym, "stock")

    # 截断 link 数量
    if len(links) > max_links:
        # 按 value 降序，截 top max_links
        links = sorted(links, key=lambda x: x.get("value", 0), reverse=True)[:max_links]
        # 清理孤立 node
        used = set()
        for ln in links:
            used.add(ln["source"])
            used.add(ln["target"])
        nodes_map = {k: v for k, v in nodes_map.items() if k in used}

    return {
        "nodes": list(nodes_map.values()),
        "links": links,
        "counts": {
            "sectors": sum(1 for n in nodes_map.values() if n["kind"] == "sector"),
            "stocks": sum(1 for n in nodes_map.values() if n["kind"] == "stock"),
            "links": len(links),
            "approved": len(approved),
            "blocked": len(blocked),
        },
        "reason": "ok",
    }
