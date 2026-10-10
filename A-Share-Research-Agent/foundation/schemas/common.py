"""溯源层契约 — 数据状态、口径、多源一致性、单条取证。

设计要点（对应设计文档 §3.3 data_quality、§2.5 schema 版本化）：
1. **三态语义必须区分**：`empty`（确实无数据）/ `unavailable_yet`（T 日盘后尚未披露）
   / `failed`（拉取失败）语义完全不同，智能体据此决定"声明不可用"还是"判定无事件"。
2. **口径不强行合并**：不同源的行业分类/复权口径是**独立证据**，标注 `caliber`
   而非统一到一个口径——保留分歧比制造假一致更诚实。
3. **多源一致性显式化**：`source_agreement` 让下游知道某个数字是单源孤证还是多源互证。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DataStatus(str, Enum):
    """单条数据/单个维度的获取状态。

    语义边界（严格区分，禁止混用）：
    - OK              成功获取到有效数据
    - EMPTY           成功获取，但业务上确实无数据（如"今日无龙虎榜"），合法语义
    - UNAVAILABLE_YET 该时点尚未披露（如盘后拉取晚间公告），**不是**"无数据"
    - FAILED          所有源拉取失败（网络/解析/熔断）
    - CONFLICT        拿到了数据，但多源分歧超阈值，数值不可信
    """

    OK = "ok"
    EMPTY = "empty"
    UNAVAILABLE_YET = "unavailable_yet"
    FAILED = "failed"
    CONFLICT = "conflict"


class SourceAgreement(str, Enum):
    """多源一致性判定。

    - SINGLE   仅一个源提供该数据（孤证）
    - AGREE    多源提供且偏差在阈值内（互证）
    - CONFLICT 多源提供但偏差超阈值（分歧）
    """

    SINGLE = "single"
    AGREE = "agree"
    CONFLICT = "conflict"


class Caliber(str, Enum):
    """口径标注 — 只标注来源，不强行统一。

    同一维度不同口径的数据作为**独立因子**并列呈现，
    由解读层看到分歧，而不是被合并后的假一致掩盖。
    """

    EASTMONEY = "eastmoney"
    SW = "sw"  # 申万行业
    THS = "ths"  # 同花顺
    TUSHARE = "tushare"
    BAOSTOCK = "baostock"
    SINA = "sina"
    AKSHARE = "akshare"
    TENCENT = "tencent"
    CNINFO = "cninfo"
    UNKNOWN = "unknown"


class SourceEvidence(BaseModel):
    """单条数据取证 — 指标包里每个数字都能回溯到"哪个源、哪个方法、什么时候取的"。"""

    source: str = Field(description="provider 名，如 'eastmoney' / 'baostock'")
    method: str = Field(description="route() 的方法名，如 'get_sector_fund_flow'")
    status: DataStatus = Field(description="获取状态，三态语义见 DataStatus")

    as_of: datetime | None = Field(
        default=None,
        description="数据本身的业务时点（如交易日、公告日）；未知为 None",
    )
    fetched_at: datetime | None = Field(
        default=None,
        description="拉取时点；用于判断是否需要二次刷新",
    )
    latency_ms: float = Field(default=0.0, ge=0)

    error: str | None = Field(default=None, description="失败原因（截断到 240 字符）")
    preview: Any = Field(default=None, description="出参摘要（不存全量）")


class FieldConflict(BaseModel):
    """同名字段的多源分歧明细 — source_agreement=CONFLICT 时必填。"""

    field: str = Field(description="分歧字段名，如 'pct_change' / 'net_inflow'")
    values: dict[str, float | str] = Field(
        description="source → 取值，如 {'eastmoney': 2.31, 'akshare': 2.28}"
    )
    deviation: float = Field(
        default=0.0, ge=0,
        description="相对偏差（max-min)/|mean|，用于阈值判定",
    )
    threshold: float = Field(default=0.01, ge=0, description="判定冲突的偏差阈值")


__all__ = [
    "DataStatus",
    "SourceAgreement",
    "Caliber",
    "SourceEvidence",
    "FieldConflict",
]
