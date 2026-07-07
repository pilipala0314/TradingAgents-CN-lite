from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel

SHARED_ANALYST_COLLAB_RULES = """你是多 Agent 投研链路中的专业分析节点。

你的职责：
1. 只对当前职责范围内的问题做判断，不越权替代其他 Agent。
2. 优先依据结构化快照、规则命中和 A 股制度背景做推理，不凭空补事实。
3. 当上位状态与局部信号冲突时，优先服从上位状态约束，并把冲突写入理由。
4. 输出必须可审计、可落库、可被下游 Agent 继续消费。"""


def format_context_block(title: str, payload: Any) -> str:
    """Render a stable JSON context block for prompts."""
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    return f"[{title}]\n{serialized}"


def render_schema_instruction(schema: type[BaseModel]) -> str:
    """Expose schema fields inside the prompt to improve structured stability."""
    properties = schema.model_json_schema().get("properties", {})
    lines = ["输出字段要求："]
    for field_name, field_meta in properties.items():
        description = field_meta.get("description", "")
        lines.append(f"- {field_name}: {description}")
    return "\n".join(lines)


def compose_prompt(
    *,
    role_instruction: str,
    schema: type[BaseModel],
    context_blocks: Mapping[str, Any],
    task_instruction: str,
) -> tuple[str, str]:
    """Compose system/user prompts with shared governance rules."""
    system_prompt = "\n\n".join(
        [
            SHARED_ANALYST_COLLAB_RULES,
            role_instruction.strip(),
            render_schema_instruction(schema),
        ]
    )
    user_sections = [task_instruction.strip()]
    for title, payload in context_blocks.items():
        user_sections.append(format_context_block(title, payload))
    user_prompt = "\n\n".join(user_sections)
    return system_prompt, user_prompt
