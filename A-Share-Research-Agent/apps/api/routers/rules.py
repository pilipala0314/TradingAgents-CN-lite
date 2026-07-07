"""Rules 端点。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from rule_engine.evaluator import RuleEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/rules", tags=["rules"])


@router.post("/evaluate")
async def evaluate_rules(payload: dict[str, Any]) -> dict[str, Any]:
    """对给定特征快照直接执行规则评估，便于回放和调试。"""
    entity_type = payload.get("entity_type", "market")
    snapshot = payload.get("snapshot", {})
    market_regime = payload.get("market_regime")
    if entity_type == "market":
        hits = RuleEngine.evaluate_market(snapshot)
    elif entity_type == "sector":
        hits = RuleEngine.evaluate_sector(snapshot, market_regime or "neutral_mixed")
    elif entity_type == "stock":
        hits = RuleEngine.evaluate_stock(snapshot)
    else:
        raise HTTPException(status_code=400, detail="unsupported entity_type")
    return {"ok": True, "hits": hits}
