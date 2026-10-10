"""L2 多源融合 — ``SourceResult`` 列表 → ``Factor``（纯函数）。

语义（实现计划已批准）：
- 只融合 ``status == OK`` 的源；failed / empty / unavailable_yet 只进 evidence；
- 偏差公式 ``deviation = (max-min)/|mean|``（schema 注释定义），超 ``fusion.conflict_threshold`` → CONFLICT；
- confidence：single=0.6（``require_agreement`` → 0.4）；agree 按独立源数 0.70/0.80/0.90；
  conflict=0.3；全失败=0.0；
- 独立源数用 ``upstream_key`` 去重（eastmoney 与 a_stock_data 同为东财上游，不虚高置信度）。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from foundation.registry import FactorSpec, FusionStrategy, SourceSpec
from foundation.schemas import (
    Caliber,
    DataStatus,
    Factor,
    FieldConflict,
    SourceAgreement,
    SourceEvidence,
)
from datasource.providers._shared.utils import to_native

__all__ = ["SourceResult", "fuse", "upstream_key"]

# provider → 上游数据源（识别"伪多源"：东财直连与 a_stock_data 同源）
_UPSTREAM = {"eastmoney": "em", "a_stock_data": "em"}

# 非数值分歧的 deviation 哨兵值（FieldConflict.deviation 需有限非负数）
_NON_NUMERIC_DEVIATION = 1.0e9

# AGREE 置信度：按去重后的独立源数封顶（伪多源不给加成）
_AGREE_CONFIDENCE = {1: 0.70, 2: 0.80}
_AGREE_CONFIDENCE_MAX = 0.90


def upstream_key(provider: str) -> str:
    """该 provider 的实际上游标识（用于伪多源识别）。"""
    return _UPSTREAM.get(provider, provider)


@dataclass
class SourceResult:
    """单源评估结果（含 OK/EMPTY/FAILED/UNAVAILABLE_YET 全状态）。"""

    spec: SourceSpec
    order: int
    status: DataStatus
    value: float | int | str | None = None
    payload: Any = None
    evidence: SourceEvidence | None = None
    note: str = ""


def fuse(spec: FactorSpec, results: list[SourceResult]) -> Factor:
    """融合一个因子的全部源结果 → Factor（含溯源与一致性标注）。"""
    ordered = sorted(results, key=lambda r: (r.spec.priority, r.order))
    ok = [r for r in ordered if r.status == DataStatus.OK]

    value: Any = None
    agreement = SourceAgreement.SINGLE
    conflicts: list[FieldConflict] = []
    confidence = 0.0
    fused_payload: Any = None

    if ok:
        value, fused_payload = _apply_strategy(spec, ok)
        confidence = 0.6
        if len(ok) >= 2:
            agreement, conflicts, confidence = _agreement(spec, ok)
        elif spec.fusion.require_agreement:
            confidence = 0.4

    caliber: Caliber = (ok[0].spec if ok else ordered[0].spec).caliber
    as_of = ok[0].evidence.as_of if (ok and ok[0].evidence) else None

    raw_by_source: dict[str, Any] = {}
    for r in ordered:
        if r.status == DataStatus.OK:
            raw_by_source[r.spec.provider] = r.payload if r.payload is not None else r.value
    if fused_payload is not None:
        raw_by_source["__fused__"] = fused_payload

    return Factor(
        id=spec.id,
        dimension=spec.dimension,
        name=spec.name,
        value=to_native(value) if value is not None else None,
        unit=spec.unit,
        caliber=caliber,
        as_of=as_of,
        confidence=confidence,
        weight=1.0,
        source_agreement=agreement,
        evidence=[r.evidence for r in ordered if r.evidence is not None],
        conflicts=conflicts,
        raw_by_source=raw_by_source,
    )


# ── 策略 ──────────────────────────────────────────────────────────

def _apply_strategy(spec: FactorSpec, ok: list[SourceResult]) -> tuple[Any, Any]:
    """按 fusion.strategy 计算融合值；返回 (value, fused_payload)。"""
    strategy = spec.fusion.strategy

    if strategy in (FusionStrategy.SINGLE, FusionStrategy.FIRST):
        return ok[0].value, None

    if strategy in (FusionStrategy.MEDIAN, FusionStrategy.MEAN):
        nums = sorted(float(r.value) for r in ok if isinstance(r.value, (int, float)))
        if not nums:
            return ok[0].value, None
        if strategy == FusionStrategy.MEAN:
            return sum(nums) / len(nums), None
        mid = len(nums) // 2
        if len(nums) % 2:
            return nums[mid], None
        return (nums[mid - 1] + nums[mid]) / 2, None

    if strategy == FusionStrategy.VOTE:
        counts = Counter(str(r.value) for r in ok)
        top = max(counts.values())
        # ok 已按 (priority, order) 排序 → 平票取优先级最高者
        winner = next(r for r in ok if counts[str(r.value)] == top)
        return winner.value, None

    if strategy == FusionStrategy.UNION_DEDUP:
        merged: list[Any] = []
        seen: set[str] = set()
        for r in ok:
            items = r.payload if isinstance(r.payload, list) else (
                [r.payload] if r.payload is not None else []
            )
            for item in items:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
        return len(merged), merged

    # 未知策略：不猜测，取优先级最高源的声明值
    return ok[0].value, None


# ── 一致性判定 ────────────────────────────────────────────────────

def _agreement(
    spec: FactorSpec, ok: list[SourceResult],
) -> tuple[SourceAgreement, list[FieldConflict], float]:
    """多源一致性：AGREE / CONFLICT + 分歧明细 + 置信度。"""
    values = [r.value for r in ok]
    threshold = spec.fusion.conflict_threshold
    n_indep = len({upstream_key(r.spec.provider) for r in ok})
    confidence = _AGREE_CONFIDENCE.get(n_indep, _AGREE_CONFIDENCE_MAX)

    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        nums = [float(v) for v in values]
        mx, mn = max(nums), min(nums)
        mean = sum(nums) / len(nums)
        if mx == mn:
            return SourceAgreement.AGREE, [], confidence
        deviation = (mx - mn) / abs(mean) if abs(mean) >= 1e-9 else 1e9
        if deviation <= threshold:
            return SourceAgreement.AGREE, [], confidence
        conflict = FieldConflict(
            field=ok[0].spec.field or spec.id,
            values={r.spec.provider: to_native(r.value) for r in ok},
            deviation=to_native(deviation),
            threshold=threshold,
        )
        return SourceAgreement.CONFLICT, [conflict], 0.3

    # 非数值（字符串/列表）：全等 → AGREE，否则 CONFLICT
    if all(str(v) == str(values[0]) for v in values):
        return SourceAgreement.AGREE, [], confidence
    conflict = FieldConflict(
        field=ok[0].spec.field or spec.id,
        values={r.spec.provider: to_native(r.value) for r in ok},
        deviation=_NON_NUMERIC_DEVIATION,
        threshold=threshold,
    )
    return SourceAgreement.CONFLICT, [conflict], 0.3