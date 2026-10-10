"""指标包契约 — 智能体的唯一数据入口。

对应设计文档 §3.3：**指标包是智能体的唯一数据入口**（铁律一）。
智能体不直接碰数据源层，只消费本结构；因此可离线回放、prompt 迭代不重拉数据。

三个关键约定：
1. **因子是主线**：`factors` 是多源融合后的结构化输入，带置信度与溯源。
2. **三态 data_quality**：`empty` / `unavailable_yet` / `failed` 必须区分，
   智能体据此声明"数据不可用"而非脑补。
3. **原始分组可裁剪**：`market` / `sectors` / `events` / `stock` 供智能体按需裁剪，
   原始 K 线不整体进 prompt（由 AgentRuntime 按 token 预算截断）。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from foundation.schemas.common import Caliber, DataStatus, SourceEvidence
from foundation.schemas.factor import Factor

SCHEMA_VERSION = "1.0"


class PackScope(str, Enum):
    """指标包的覆盖范围。"""

    MARKET = "market"  # 大盘
    SECTOR = "sector"  # 单板块（本阶段核心）
    STOCK = "stock"  # 单标的
    MULTI_SECTOR = "multi_sector"  # 全板块


class PackMeta(BaseModel):
    """指标包元信息 — 幂等重放与断点续跑的依据。"""

    schema_version: str = SCHEMA_VERSION
    generated_at: datetime = Field(default_factory=datetime.now)
    trade_date: str = Field(description="交易日 YYYY-MM-DD")
    scope: PackScope
    symbols: list[str] = Field(default_factory=list, description="涉及的标的代码（已归一）")
    sector: str | None = Field(default=None, description="scope=sector 时的板块名")
    sector_caliber: Caliber = Field(
        default=Caliber.UNKNOWN,
        description="板块口径来源（东财/申万/同花顺），不做跨口径合并",
    )
    produced_by: str = Field(default="indicators", description="产出方标识")
    generation_round: int = Field(
        default=1, ge=1,
        description="第几轮拉取；盘后两轮刷新用（1=盘后首轮 2=延迟数据补拉）",
    )


class DataQualityEntry(BaseModel):
    """某个数据维度的质量声明 — 智能体必须读这个再决定能不能下结论。"""

    dimension: str = Field(description="维度名，如 'capital' / 'dragon_tiger' / 'northbound'")
    status: DataStatus
    as_of: datetime | None = None
    note: str = Field(default="", description="补充说明，如 '龙虎榜预计 19:00 披露'")
    evidence: list[SourceEvidence] = Field(default_factory=list)

    @property
    def blocks_conclusion(self) -> bool:
        """该维度是否阻塞结论（缺失/未披露/冲突 → 只能声明不可用）。"""
        return self.status in {
            DataStatus.UNAVAILABLE_YET,
            DataStatus.FAILED,
            DataStatus.CONFLICT,
        }


class IndicatorPack(BaseModel):
    """指标包 — 智能体唯一输入。

    落盘位置：`reports/<date>/artifacts/indicators/<name>.json`（设计文档 §3.4）。
    写入需原子化（tmp + os.replace），重跑覆盖不叠加。
    """

    meta: PackMeta

    # ── 溯源总账 ────────────────────────────────────────────────
    data_quality: list[DataQualityEntry] = Field(default_factory=list)

    # ── 因子层（多源融合后，智能体的结构化主线）──────────────────
    factors: list[Factor] = Field(default_factory=list)

    # ── 原始分组（可裁剪，供智能体补充上下文）──────────────────
    market: dict[str, Any] = Field(default_factory=dict)
    sectors: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    stock: dict[str, Any] = Field(default_factory=dict)

    def factors_by_dimension(self) -> dict[str, list[Factor]]:
        """按维度分组的因子视图。"""
        out: dict[str, list[Factor]] = {}
        for f in self.factors:
            out.setdefault(f.dimension.value, []).append(f)
        return out

    def blocked_dimensions(self) -> list[str]:
        """被阻塞的维度清单 — 智能体必须声明"数据不可用"的范围。"""
        return [e.dimension for e in self.data_quality if e.blocks_conclusion]

    def usable_factors(self) -> list[Factor]:
        """可作为事实依据的因子（剔除冲突因子与空值因子）。"""
        return [f for f in self.factors if f.is_usable]

    def summary(self) -> dict[str, Any]:
        """给报告附录用的质量摘要（透明化数据可信度）。"""
        return {
            "trade_date": self.meta.trade_date,
            "scope": self.meta.scope.value,
            "sector": self.meta.sector,
            "sector_caliber": self.meta.sector_caliber.value,
            "factors_total": len(self.factors),
            "factors_usable": len(self.usable_factors()),
            "factors_conflict": sum(
                1 for f in self.factors if f.source_agreement.value == "conflict"
            ),
            "dimensions_blocked": self.blocked_dimensions(),
            "generation_round": self.meta.generation_round,
        }


__all__ = ["SCHEMA_VERSION", "PackScope", "PackMeta", "DataQualityEntry", "IndicatorPack"]
