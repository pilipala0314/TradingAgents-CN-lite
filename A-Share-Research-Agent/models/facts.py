import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base


class FactMarketBar(Base):
    __tablename__ = "fact_market_bar"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "bar_date",
            "bar_timeframe",
            "adj_type",
            "version",
            name="uq_fact_market_bar_security_date_timeframe_adj_version",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    security_id = Column(UUID(as_uuid=True), nullable=False)
    bar_date = Column(Date, nullable=False)
    bar_timeframe = Column(String(16), nullable=False)
    open = Column(Numeric(20, 6), nullable=False)
    high = Column(Numeric(20, 6), nullable=False)
    low = Column(Numeric(20, 6), nullable=False)
    close = Column(Numeric(20, 6), nullable=False)
    volume = Column(Numeric(24, 4), nullable=True)
    turnover = Column(Numeric(24, 4), nullable=True)
    turnover_rate = Column(Numeric(12, 6), nullable=True)
    adj_type = Column(String(16), nullable=False, default="none")
    source = Column(String(32), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    # P5.3 多租户（nullable：历史数据可能没填；新数据默认 default tenant）
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FactCapitalFlow(Base):
    __tablename__ = "fact_capital_flow"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "business_date",
            "window_type",
            "flow_type",
            "version",
            name="uq_fact_capital_flow_entity_date_window_type_version",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    entity_type = Column(String(16), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    flow_type = Column(String(32), nullable=False)
    net_inflow = Column(Numeric(24, 4), nullable=True)
    gross_inflow = Column(Numeric(24, 4), nullable=True)
    gross_outflow = Column(Numeric(24, 4), nullable=True)
    source = Column(String(32), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    # P5.3
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FactEvent(Base):
    __tablename__ = "fact_event"
    __table_args__ = (
        UniqueConstraint(
            "event_scope",
            "entity_id",
            "published_at",
            "source",
            "title",
            name="uq_fact_event_scope_entity_published_source_title",
        ),
    )

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type = Column(String(32), nullable=False)
    event_scope = Column(String(16), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    title = Column(String(512), nullable=False)
    summary = Column(Text, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=False)
    captured_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    importance_score = Column(Numeric(8, 4), nullable=True)
    sentiment_score = Column(Numeric(8, 4), nullable=True)
    risk_score = Column(Numeric(8, 4), nullable=True)
    severity = Column(String(16), nullable=True)
    source = Column(String(32), nullable=False)
    source_url = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    # P5.3
    tenant_id = Column(String(64), nullable=True, index=True)


class FactEventRaw(Base):
    """事件原始层 — 事件 ETL 落库前先入此表，用于审计 / 重放 / 修复。

    与 `fact_event` 的关系：
    - `fact_event_raw`  保留抓取到的原始 dict（JSONB）+ 业务日，便于失败回放
    - `fact_event`      标准化后的事件（含 entity_id / severity / score）
    """

    __tablename__ = "fact_event_raw"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            name="uq_fact_event_raw_event_id",
        ),
    )

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_date = Column(Date, nullable=False)
    title = Column(String(512), nullable=False)
    summary = Column(Text, nullable=True)
    source = Column(String(32), nullable=False)
    source_url = Column(Text, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=False)
    captured_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    raw_payload = Column(JSONB, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    # P5.3
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FactRuleHit(Base):
    __tablename__ = "fact_rule_hit"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "business_date",
            "window_type",
            "rule_code",
            "rule_version",
            name="uq_fact_rule_hit_entity_date_window_rule_version",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    entity_type = Column(String(16), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    rule_code = Column(String(64), nullable=False)
    rule_level = Column(String(16), nullable=False)
    hit_flag = Column(Boolean, nullable=False, default=True)
    hit_value = Column(String(256), nullable=True)
    action = Column(String(32), nullable=False)
    message = Column(Text, nullable=True)
    rule_version = Column(Integer, nullable=False, default=1)
    # P5.3
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FactFinancialReport(Base):
    """P2-T3 财报关键指标事实表（每只股票 × 报告期 → 一行）。

    数据源：akshare `stock_financial_analysis_indicator_em`（主要财务指标）
    字段采用宽松 Numeric(20, 4)，允许 None（缺失值）。
    唯一键：(security_id, report_period, indicator_code, version)
    """
    __tablename__ = "fact_financial_report"
    __table_args__ = (
        UniqueConstraint(
            "security_id",
            "report_period",
            "indicator_code",
            "version",
            name="uq_fact_financial_report_security_period_indicator_version",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    security_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    report_period = Column(Date, nullable=False, index=True)
    indicator_code = Column(String(64), nullable=False)
    indicator_name = Column(String(128), nullable=True)
    value = Column(Numeric(20, 4), nullable=True)
    unit = Column(String(16), nullable=True)
    source = Column(String(32), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FactNewsItem(Base):
    """P2-T5 舆情新闻条目表（一行 = 一条新闻）。

    数据源：akshare `stock_news_em`（东财新闻流）。
    - url 是主键判重字段（同一 URL 不重复入库）
    - sentiment_score: -1..+1 (P2-T5 词典打分，LLM 增强在 P5)
    - sentiment_label: positive / neutral / negative
    """
    __tablename__ = "fact_news_item"
    __table_args__ = (
        UniqueConstraint("url", name="uq_fact_news_item_url"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    security_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    symbol = Column(String(16), nullable=True, index=True)
    news_id = Column(String(64), nullable=True, index=True)
    title = Column(Text, nullable=False)
    content = Column(Text, nullable=True)
    url = Column(String(512), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True, index=True)
    source_name = Column(String(64), nullable=True)
    sentiment_score = Column(Numeric(6, 4), nullable=True)
    sentiment_label = Column(String(16), nullable=True)
    score_method = Column(String(32), nullable=True)
    raw_payload = Column(JSONB, nullable=True)
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class OpsJobRun(Base):
    __tablename__ = "ops_job_run"

    job_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_type = Column(String(32), nullable=False)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    idempotency_key = Column(String(128), unique=True, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    started_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    input_version = Column(Integer, nullable=False, default=1)
    feature_version = Column(Integer, nullable=True)
    rule_version = Column(Integer, nullable=True)
    prompt_bundle_version = Column(Integer, nullable=True)
    model_version = Column(String(64), nullable=True)
    output_version = Column(Integer, nullable=False, default=1)
    output_ref_type = Column(String(32), nullable=True)
    output_ref_id = Column(UUID(as_uuid=True), nullable=True)
    error_message = Column(Text, nullable=True)
    trace_payload = Column(JSONB, nullable=True)
