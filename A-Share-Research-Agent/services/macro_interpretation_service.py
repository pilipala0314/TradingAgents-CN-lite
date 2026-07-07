"""MacroInterpretationService — P0-T6。

职责：
- 接 4 个宏观端点（PMI / CPI / M2 / 国债利差）的实时值 + 3 个月历史
- 用 LLM 解读整体宏观立场和分项观点
- 失败语义：LLM 不可用 → 返回 {interpretation: None, llm_error: "..."}，由前端占位渲染
"""
from __future__ import annotations

import logging
from typing import Any

from core.config import settings
from core.llm.client import LLMUnavailable
from core.llm.factory import create_llm_client
from core.llm.structured import bind_structured_output, invoke_structured_or_text
from prompts.macro_prompts import render_macro_interpretation_prompt
from schemas.agent_io import MacroInterpretationOutput

logger = logging.getLogger(__name__)


def _build_macro_snapshot_payload(snapshot: Any) -> dict[str, Any]:
    """把 MacroSnapshot 序列化成 dict 供 LLM 消费。"""
    payload: dict[str, Any] = {}
    for field in ("pmi", "cpi", "m2", "bond_yield"):
        snap = getattr(snapshot, field, None)
        if snap is None:
            payload[field] = None
            continue
        payload[field] = {
            "available": getattr(snap, "available", False),
            "value": getattr(snap, "value", None),
            "as_of": getattr(snap, "as_of", None),
            "context": getattr(snap, "context", ""),
        }
    payload["regime_hint"] = getattr(snapshot, "regime_hint", None)
    return payload


async def interpret_macro_snapshot(
    *,
    snapshot: Any,
    history_by_indicator: dict[str, list[dict[str, Any]]] | None = None,
    regime_hint: str | None = None,
) -> dict[str, Any]:
    """返回 {interpretation, llm_status, llm_error} 字典。

    失败语义：LLM 不可用 / 调用失败 → interpretation=None, llm_error=<reason>。
    """
    if not getattr(settings, "LLM_API_KEY", None):
        return {
            "interpretation": None,
            "llm_status": "unconfigured",
            "llm_error": "LLM_API_KEY 未配置",
        }
    payload = _build_macro_snapshot_payload(snapshot)
    history = history_by_indicator or {}
    system, user = render_macro_interpretation_prompt(
        macro_snapshot=payload,
        history_by_indicator=history,
        regime_hint=regime_hint or getattr(snapshot, "regime_hint", None),
    )
    try:
        client = create_llm_client(
            provider=settings.LLM_PROVIDER,
            model_name=settings.LLM_MODEL,
            temperature=0.1,
            max_tokens=1500,
        )
        plain_llm = client.get_langchain_llm()
        structured_llm = bind_structured_output(plain_llm, MacroInterpretationOutput, agent_name="MacroInterpreter")
        if structured_llm is None:
            return {
                "interpretation": None,
                "llm_status": "unsupported",
                "llm_error": f"{settings.LLM_MODEL} 不支持结构化输出",
            }
        result = await invoke_structured_or_text(
            structured_llm=structured_llm,
            plain_llm=plain_llm,
            messages=[
                ("system", system),
                ("human", user),
            ],
            agent_name="MacroInterpreter",
        )
        # 结果可能是 pydantic 实例 / dict
        if isinstance(result, MacroInterpretationOutput):
            interpretation = result.model_dump()
        elif isinstance(result, dict):
            interpretation = result
        else:
            interpretation = None
        return {
            "interpretation": interpretation,
            "llm_status": "ok",
            "llm_error": None,
        }
    except LLMUnavailable as exc:
        logger.warning("MacroInterpretation: LLM 不可用: %s", exc)
        return {"interpretation": None, "llm_status": "unavailable", "llm_error": str(exc)}
    except Exception as exc:
        logger.warning("MacroInterpretation: LLM 调用失败: %s", exc)
        return {"interpretation": None, "llm_status": "error", "llm_error": str(exc)[:200]}
