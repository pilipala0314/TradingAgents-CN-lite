"""因子层契约 — 多源融合后的单因子。

一个因子 = 同一业务含义（如"板块主力净流入"）在多源交叉校验后的产物。
核心设计：**因子必须带置信度与溯源**，让智能体知道"这个数字有多可信"，
而不是把单源孤证当成事实。对应设计文档 §3.3 data_quality、§2.4 铁律一。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from foundation.schemas.common import (
    Caliber,
    FieldConflict,
    SourceAgreement,
    SourceEvidence,
)


class FactorDimension(str, Enum):
    """因子维度 — 智能体解读的输入分组依据。"""

    PRICE = "price"  # 价格 / 技术形态
    CAPITAL = "capital"  # 资金面（主力/北向/龙虎榜）
    SENTIMENT = "sentiment"  # 情绪面（涨跌家数/舆情/热度）
    EVENT = "event"  # 事件面（政策/公告/地缘）
    FUNDAMENTAL = "fundamental"  # 基本面（估值/盈利）
    MARKET = "market"  # 大盘环境（指数/广度/宏观）


class Factor(BaseModel):
    """单个因子 — 多源融合产物。

    置信度来源（合成规则由 L2 指标层实现，schema 只承载结果）：
    - `source_agreement == AGREE`    → 高置信（多源互证）
    - `source_agreement == SINGLE`   → 中等置信（孤证，标注给智能体）
    - `source_agreement == CONFLICT` → 低置信，`conflicts` 必填明细
    """

    id: str = Field(description="因子唯一标识，如 'sector_capital_net_inflow'")
    dimension: FactorDimension
    name: str = Field(description="人类可读名称，如 '板块主力净流入'")

    value: float | int | str | None = Field(default=None, description="因子取值")
    unit: str = Field(default="", description="单位：元/亿元/百分比/无量纲")
    caliber: Caliber = Field(default=Caliber.UNKNOWN, description="口径来源（不强行统一）")
    as_of: datetime | None = Field(default=None, description="业务时点")

    # ── 融合产出 ──────────────────────────────────────────────
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="因子置信度 0-1；由源数量与一致性合成",
    )
    weight: float = Field(
        default=1.0, ge=0.0,
        description="同维度合成时的权重；不参与跨维度比较",
    )
    source_agreement: SourceAgreement = Field(default=SourceAgreement.SINGLE)

    # ── 溯源 ──────────────────────────────────────────────────
    evidence: list[SourceEvidence] = Field(
        default_factory=list,
        description="参与该因子的每条取证；多源时按优先级排列",
    )
    conflicts: list[FieldConflict] = Field(
        default_factory=list,
        description="多源分歧明细；source_agreement=CONFLICT 时必填",
    )

    # ── 可选：原始多源视图（供审计/回放，可不进 prompt）────────
    raw_by_source: dict[str, Any] = Field(
        default_factory=dict,
        description="source → 原始取值，仅落盘审计用，默认不进 prompt",
    )

    @property
    def is_usable(self) -> bool:
        """智能体是否可用该因子做结论（冲突因子只可作为"分歧提示"，不做事实依据）。"""
        return self.source_agreement != SourceAgreement.CONFLICT and self.value is not None

    def audit_trail(self) -> dict[str, Any]:
        """压缩为可序列化的溯源视图（写入结论包附录）。"""
        return {
            "id": self.id,
            "value": self.value,
            "unit": self.unit,
            "caliber": self.caliber.value,
            "confidence": self.confidence,
            "source_agreement": self.source_agreement.value,
            "sources": [e.source for e in self.evidence],
            "conflict_fields": [c.field for c in self.conflicts],
        }


__all__ = ["FactorDimension", "Factor"]
