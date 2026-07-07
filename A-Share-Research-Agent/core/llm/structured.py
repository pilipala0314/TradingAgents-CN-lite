from __future__ import annotations

import logging
from typing import Any, TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def bind_structured_output(llm: Any, schema: type[T], *, agent_name: str) -> Any | None:
    """Bind schema-aware structured output when the provider supports it."""
    try:
        return llm.with_structured_output(schema)
    except (NotImplementedError, AttributeError) as exc:
        logger.warning(
            "%s 不支持 with_structured_output，降级为普通文本生成: %s",
            agent_name,
            exc,
        )
        return None


async def invoke_structured_or_text(
    *,
    structured_llm: Any | None,
    plain_llm: Any,
    messages: list[Any],
    agent_name: str,
) -> Any:
    """Try structured invocation first and fall back to plain text once."""
    if structured_llm is not None:
        try:
            return await structured_llm.ainvoke(messages)
        except Exception as exc:
            logger.warning(
                "%s 结构化调用失败，降级为普通文本输出: %s",
                agent_name,
                exc,
            )

    return await plain_llm.ainvoke(messages)
