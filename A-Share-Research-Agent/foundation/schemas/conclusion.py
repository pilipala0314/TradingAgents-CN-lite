"""结论包契约 — 解读智能体/裁判的结构化输出。

对应设计文档 §5.2 红蓝对抗、§6.1 事实核查：
1. **强制立场**：裁判必须给出 stance + confidence，禁止骑墙。
2. **数字可溯源**：结论中引用的每个数字都绑定 `factor_id`，
   评测层据此机检；找不到对应因子 → 打回。
3. **缺失显式声明**：`unavailable_dimensions` 让下游知道哪些维度没参与结论。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Stance(str, Enum):
    """立场 — 裁判必须承诺，不得默认持有。"""

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"  # 合法结论，但须满足触发条件（见设计文档 §5.2）


class CitedValue(BaseModel):
    """结论中引用的一个具体数字 — 数字溯源的最小单元。"""

    factor_id: str = Field(description="绑定到 IndicatorPack.factors 中的因子 id")
    value: float | str = Field(description="结论里出现的数值")
    unit: str = Field(default="", description="单位；与因子 unit 对齐")
    quote: str = Field(default="", description="结论中的原文片段（用于模糊匹配校验）")


class FactorUse(BaseModel):
    """某因子在结论中的使用情况 — 供评测层判断"论据充分性"。"""

    factor_id: str
    weight_given: float = Field(
        default=0.0, ge=0.0,
        description="智能体实际给该因子的权重（0-1，同维度内归一）",
    )
    cited_values: list[CitedValue] = Field(default_factory=list)
    role: str = Field(
        default="support",
        description="support=支持 / counter=反驳 / context=背景",
    )


class ConclusionPack(BaseModel):
    """单个解读智能体的结论包。"""

    schema_version: str = "1.0"
    produced_by: str = Field(description="agent 标识，如 'sector_interpreter' / 'judge'")
    produced_at: datetime = Field(default_factory=datetime.now)
    trade_date: str
    scope: str = Field(description="market | sector | stock")
    subject: str = Field(default="", description="板块名/标的代码")

    # ── 核心结论 ────────────────────────────────────────────────
    stance: Stance
    confidence: float = Field(ge=0.0, le=1.0, description="置信度 0-1，必须给出")
    conclusion: str = Field(min_length=1, description="结论正文，禁止空")

    key_arguments: list[str] = Field(default_factory=list, description="关键论据")
    risks: list[str] = Field(default_factory=list, description="风险提示")
    catalysts: list[str] = Field(default_factory=list, description="潜在催化")

    # ── 数字溯源 ────────────────────────────────────────────────
    factors_used: list[FactorUse] = Field(default_factory=list)
    cited_values: list[CitedValue] = Field(
        default_factory=list,
        description="自由文本结论中出现的数字（机检用）",
    )

    # ── 数据质量声明 ────────────────────────────────────────────
    unavailable_dimensions: list[str] = Field(
        default_factory=list,
        description="本次未参与结论的维度（数据缺失/未披露/冲突）",
    )
    hallucination_risk: bool = Field(
        default=False,
        description="评测层机检发现未能溯源的数字时置 True，强制修订",
    )

    def cited_factor_ids(self) -> set[str]:
        ids = {c.factor_id for c in self.cited_values}
        for fu in self.factors_used:
            ids.add(fu.factor_id)
            ids.update(c.factor_id for c in fu.cited_values)
        return ids


class DebateVerdict(BaseModel):
    """红蓝对抗裁决 — Judge 的输出。

    硬约束（设计文档 §5.2）：
    - 必须给出 stance + confidence，**禁止"双方都有道理"式骑墙**
    - `neutral` 合法但须满足触发条件：双方关键论据均未被推翻且信号相互抵消
    - 不得因发言顺序（最后印象）加权裁决
    """

    schema_version: str = "1.0"
    produced_by: str = "judge"
    produced_at: datetime = Field(default_factory=datetime.now)
    trade_date: str
    scope: str
    subject: str = ""

    stance: Stance
    confidence: float = Field(ge=0.0, le=1.0)
    conclusion: str = Field(min_length=1)

    key_arguments: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    # ── 对抗过程留痕 ────────────────────────────────────────────
    rounds: int = Field(default=3, ge=1, le=4, description="总发言次数（Bull 起手、红蓝交替）")
    bull_points: list[str] = Field(default_factory=list)
    bear_points: list[str] = Field(default_factory=list)
    unterminated: bool = Field(
        default=False,
        description="True=未经对抗（某方失败降级为单方陈述），报告须注明",
    )
    neutral_justification: str = Field(
        default="",
        description="stance=neutral 时必填：双方论据均未被推翻的依据",
    )

    # ── 数字溯源（复用结论包口径）───────────────────────────────
    cited_values: list[CitedValue] = Field(default_factory=list)
    unavailable_dimensions: list[str] = Field(default_factory=list)


__all__ = ["Stance", "CitedValue", "FactorUse", "ConclusionPack", "DebateVerdict"]
