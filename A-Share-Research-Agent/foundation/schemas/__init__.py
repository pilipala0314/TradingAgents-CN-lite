"""层间共享 schema — 指标包 / 结论包 / 溯源契约。

这是 L1-L5 唯一的共享物（设计文档 §2.5）：层间只通过本 schema + 落盘产物通信。

导入约定::

    from foundation.schemas import (
        IndicatorPack, Factor, ConclusionPack, DebateVerdict,
        SourceEvidence, DataStatus, SourceAgreement, Caliber,
    )
"""
from foundation.schemas.common import (
    Caliber,
    DataStatus,
    FieldConflict,
    SourceAgreement,
    SourceEvidence,
)
from foundation.schemas.conclusion import (
    ConclusionPack,
    CitedValue,
    DebateVerdict,
    FactorUse,
    Stance,
)
from foundation.schemas.factor import Factor, FactorDimension
from foundation.schemas.indicator_pack import (
    SCHEMA_VERSION,
    DataQualityEntry,
    IndicatorPack,
    PackMeta,
    PackScope,
)

__all__ = [
    # 溯源层
    "DataStatus",
    "SourceAgreement",
    "Caliber",
    "SourceEvidence",
    "FieldConflict",
    # 因子层
    "FactorDimension",
    "Factor",
    # 指标包
    "SCHEMA_VERSION",
    "PackScope",
    "PackMeta",
    "DataQualityEntry",
    "IndicatorPack",
    # 结论包
    "Stance",
    "CitedValue",
    "FactorUse",
    "ConclusionPack",
    "DebateVerdict",
]
