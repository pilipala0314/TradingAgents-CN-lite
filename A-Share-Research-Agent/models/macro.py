"""宏观指标事实表 — P0-T5 持久化层。

- 4 个宏观端点（PMI / CPI / M2 / 国债利差）按 (indicator_code, period) 落库
- period 格式 "YYYY-MM"（月度指标）或 "YYYY-MM-DD"（日度指标如 PMI / 利率）
- 多租户：tenant_id 沿用事实表统一规范
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base


# 4 个宏观指标枚举
class MacroIndicatorCode:
    """宏观指标 code（与 services.macro_service 的端点对齐）。"""
    PMI_MFG = "PMI_MFG"            # 制造业 PMI
    PMI_NON_MFG = "PMI_NON_MFG"    # 非制造业 PMI
    CPI_YOY = "CPI_YOY"            # CPI 同比
    CPI_MOM = "CPI_MOM"            # CPI 环比
    PPI_YOY = "PPI_YOY"            # PPI 同比
    M2_YOY = "M2_YOY"              # M2 同比
    CN10Y_YIELD = "CN10Y_YIELD"    # 中国 10Y 国债收益率
    US10Y_YIELD = "US10Y_YIELD"    # 美国 10Y 国债收益率
    CN_US_SPREAD = "CN_US_SPREAD"  # 中美 10Y 利差


class FactMacroIndicator(Base):
    __tablename__ = "fact_macro_indicator"
    __table_args__ = (
        UniqueConstraint(
            "indicator_code", "period", "version", "tenant_id",
            name="uq_fact_macro_indicator_natural_key",
        ),
        Index("ix_fact_macro_indicator_code_asof", "indicator_code", "as_of"),
        Index("ix_fact_macro_indicator_tenant", "tenant_id"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    indicator_code = Column(String(32), nullable=False)
    period = Column(String(16), nullable=False)        # "2026-07" or "2026-07-07"
    value = Column(Float, nullable=True)
    as_of = Column(String(32), nullable=True)          # 源数据公布时间
    source = Column(String(64), nullable=False)        # "akshare.macro_china_pmi_year" 等
    version = Column(String(32), nullable=False)       # ETL 版本
    payload = Column(JSONB, nullable=True)             # 原始行（PMI/CPI/M2 完整字段）
    tenant_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
