"""Macro 解读 prompt — P0-T6。

基于 4 个宏观端点（PMI / CPI / M2 / 中美利差）的实时快照 + 3 个月趋势，
要求 LLM 输出结构化的宏观立场和分项观点。
"""
from __future__ import annotations

from typing import Any

from prompts.base import compose_prompt
from schemas.agent_io import MacroInterpretationOutput


_MACRO_ROLE_PROMPT = """你是中国 A 股市场的宏观策略分析师（Macro Interpretation Analyst）。
你只对当前宏观环境做综合判断，不替代板块/个股层面的决策。
请结合 4 个核心指标（PMI、CPI 同比、M2 同比、中美 10Y 利差）的最新值和近期趋势，
判断当前是进攻 / 谨慎 / 中性 / 防守 / 恐慌中的哪一档，并给出分项观点。

注意：
1. PMI > 50 表示制造业扩张；< 50 表示收缩。
2. CPI 同比 2-3% 是温和通胀；> 3% 警惕滞胀；< 0 警惕通缩。
3. M2 同比 8-12% 是健康区间；过高警惕泡沫，过低警惕流动性陷阱。
4. 中美 10Y 利差（CN - US）正值代表利差舒适，负值意味着资金外流压力。"""


def render_macro_interpretation_prompt(
    *,
    macro_snapshot: dict[str, Any],
    history_by_indicator: dict[str, list[dict[str, Any]]],
    regime_hint: str | None = None,
) -> tuple[str, str]:
    """拼 macro 解读 prompt。"""
    return compose_prompt(
        role_instruction=_MACRO_ROLE_PROMPT,
        schema=MacroInterpretationOutput,
        task_instruction=(
            "请基于当前宏观快照和近 3 个月趋势，给出综合宏观立场和分项解读。"
            "若指标不可用，请在 caveats 字段中注明，不要凭空假设。"
        ),
        context_blocks={
            "宏观指标快照": macro_snapshot,
            "近 3 月趋势": history_by_indicator,
            "市场阶段 hint（非强制）": regime_hint or "无",
        },
    )
