import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base


class DimSecurity(Base):
    __tablename__ = "dim_security"

    security_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol = Column(String(32), unique=True, index=True, nullable=False)
    raw_symbol = Column(String(32), nullable=False)
    security_type = Column(String(16), nullable=False)
    exchange = Column(String(16), nullable=False)
    name = Column(String(128), nullable=False)
    list_date = Column(Date, nullable=True)
    delist_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class DimSector(Base):
    __tablename__ = "dim_sector"
    __table_args__ = (
        UniqueConstraint("sector_code", "source", name="uq_dim_sector_sector_code_source"),
    )

    sector_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sector_code = Column(String(64), nullable=False)
    sector_name = Column(String(128), nullable=False)
    sector_type = Column(String(16), nullable=False)
    parent_sector_id = Column(UUID(as_uuid=True), ForeignKey("dim_sector.sector_id"), nullable=True)
    source = Column(String(32), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class MapSecuritySector(Base):
    __tablename__ = "map_security_sector"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "sector_id",
            "effective_from",
            name="uq_map_security_sector_current_mapping",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    security_id = Column(UUID(as_uuid=True), ForeignKey("dim_security.security_id"), nullable=False)
    sector_id = Column(UUID(as_uuid=True), ForeignKey("dim_sector.sector_id"), nullable=False)
    relation_type = Column(String(16), nullable=False, default="member")
    concept_purity_score = Column(Numeric(8, 4), nullable=True)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class DimModel(Base):
    """P2-T7 模型注册表 — 配置驱动 A/B 的载体。

    一行 = 一个模型实例（按 role + version 唯一）。
    - role: agent role，如 "market_analyst" / "sector_analyst" / "stock_analyst" / "committee"
    - version: 模型版本号
    - is_active: 当前是否启用（每个 role 只能有一个 active）
    - config_json: 该实例的配置（参数 / 提示词 / 温度等）
    - weight: 加权（用于多模型 ensemble）
    """
    __tablename__ = "dim_model"
    __table_args__ = (
        UniqueConstraint("role", "version", name="uq_dim_model_role_version"),
    )

    model_id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False)
    role = Column(String(64), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    model_provider = Column(String(64), nullable=True)  # openai / anthropic / deepseek / local
    model_name = Column(String(128), nullable=True)     # gpt-4o / claude-3.5 / ...
    is_active = Column(Boolean, nullable=False, default=False, index=True)
    weight = Column(Numeric(6, 4), nullable=False, default=Decimal("1.0"))
    config_json = Column(JSONB, nullable=True)
    description = Column(Text, nullable=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
