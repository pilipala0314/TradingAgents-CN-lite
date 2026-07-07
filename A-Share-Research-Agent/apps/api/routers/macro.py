"""宏观数据 API 端点。

`GET /v1/macro/snapshot`             — 4 个宏观端点最新快照 + 市场态 hint
`GET /v1/macro/interpretation`        — P0-T6 LLM 解读当前宏观

设计：
- 复用 DataRouter（享受 cache / stats / timeout / strict）
- macro 端点在 EXTENDED，开启方式：
  ```python
  from data_pipeline.data_router import DataRouter
  router = DataRouter(include_extended=True)
  ```
- 返回结构化 dict，便于 LLM 调用或 dashboard 直接渲染
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from services.macro_interpretation_service import interpret_macro_snapshot
from services.macro_service import (
    build_market_regime_from_macro,
    get_macro_snapshot,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/macro", tags=["macro"])


@router.get("/snapshot")
async def macro_snapshot(
    include_regime: bool = Query(True, description="是否附加市场态 hint 推断"),
) -> dict[str, Any]:
    """宏观快照（PMI / CPI / M2 / 10Y 国债）。

    4 个端点并发拉取，任一失败非阻塞（标记 failed），其他正常返回。
    """
    try:
        snap = await get_macro_snapshot()
    except Exception as exc:  # pragma: no cover
        logger.exception("宏观快照拉取失败")
        raise HTTPException(status_code=502, detail=f"宏观数据拉取失败: {exc}") from exc

    payload: dict[str, Any] = snap.to_dict()
    if include_regime:
        payload["regime_hint"] = build_market_regime_from_macro(snap)
    return payload


@router.get("/interpretation")
async def macro_interpretation() -> dict[str, Any]:
    """P0-T6: LLM 解读当前宏观环境。

    返回结构：
    ```json
    {
      "snapshot": { ... 同 /snapshot ... },
      "interpretation": {...} | null,
      "llm_status": "ok" | "unconfigured" | "unsupported" | "unavailable" | "error",
      "llm_error": "..." | null
    }
    ```
    """
    try:
        snap = await get_macro_snapshot()
    except Exception as exc:
        logger.exception("宏观快照拉取失败")
        raise HTTPException(status_code=502, detail=f"宏观数据拉取失败: {exc}") from exc

    regime_hint = build_market_regime_from_macro(snap)
    interpretation_payload = await interpret_macro_snapshot(
        snapshot=snap,
        history_by_indicator={},  # P2 可扩展为 3 月趋势
        regime_hint=regime_hint,
    )
    return {
        "snapshot": {**snap.to_dict(), "regime_hint": regime_hint},
        **interpretation_payload,
    }
