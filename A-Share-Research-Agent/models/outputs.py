import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from .base import Base


class FactRiskAlert(Base):
    __tablename__ = "fact_risk_alert"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "business_date",
            "window_type",
            "alert_type",
            "output_version",
            name="uq_fact_risk_alert_entity_date_window_type_version",
        ),
    )

    alert_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(String(16), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    impact_horizons = Column(JSONB, nullable=False, default=list)
    alert_type = Column(String(32), nullable=False)
    alert_level = Column(String(16), nullable=False)
    risk_scope = Column(String(16), nullable=False)
    recommended_action = Column(String(32), nullable=False)
    headline = Column(String(256), nullable=False)
    description = Column(Text, nullable=True)
    max_position = Column(Numeric(8, 4), nullable=True)
    confidence = Column(Numeric(8, 4), nullable=False)
    supporting_evidence = Column(JSONB, nullable=False, default=list)
    output_version = Column(Integer, nullable=False, default=1)
    # P5.3
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FactStrategyOutput(Base):
    __tablename__ = "fact_strategy_output"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "business_date",
            "window_type",
            "horizon",
            "strategy_type",
            "output_version",
            name="uq_fact_strategy_output_entity_date_window_horizon_type_version",
        ),
    )

    strategy_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(String(16), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    horizon = Column(String(16), nullable=False)
    direction = Column(String(16), nullable=False)
    strategy_type = Column(String(32), nullable=False)
    expected_return_low = Column(Numeric(12, 6), nullable=True)
    expected_return_high = Column(Numeric(12, 6), nullable=True)
    expected_drawdown_low = Column(Numeric(12, 6), nullable=True)
    expected_drawdown_high = Column(Numeric(12, 6), nullable=True)
    max_position = Column(Numeric(8, 4), nullable=True)
    confidence = Column(Numeric(8, 4), nullable=False)
    supporting_evidence = Column(JSONB, nullable=False, default=list)
    invalid_conditions = Column(JSONB, nullable=False, default=list)
    feature_version = Column(Integer, nullable=False, default=1)
    rule_version = Column(Integer, nullable=False, default=1)
    prompt_version = Column(Integer, nullable=False, default=1)
    prompt_bundle_version = Column(Integer, nullable=False, default=1)
    model_version = Column(String(64), nullable=True)
    output_payload = Column(JSONB, nullable=False, default=dict)
    output_version = Column(Integer, nullable=False, default=1)
    # P5.3
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FactDecisionTrace(Base):
    """决策 trace 表 — 记录每次"最终决策者"（committee）的完整 trace。

    设计目的（与 fact_strategy_output 互补）：
    - fact_strategy_output  落"决策结果"（verdict / approved / blocked 4 类信号）
    - fact_decision_trace   落"决策过程"（规则 baseline / LLM 原始 / 异常 / fallback / model）

    唯一约束：(business_date, window_type, decision_layer, model_version)
    - 同 (date, window, layer, model) 多次跑 upsert（LLM 重跑 / 修正 verdict 仍可追溯）
    - 决策层只接 committee，未来 sector_ranking_synthesizer 也可扩

    字段语义：
    - decision_layer           决策层标记（"committee" / 未来 "sector_ranking" / "stock_committee"）
    - market_regime            入参 market_outlook 的市场态（便于按 regime 切分归因）
    - verdict / confidence     最终决策
    - fallback_used            True = LLM 不可用/失败，使用规则 baseline
    - rule_baseline            规则引擎的 baseline dict（透传 structured_output 字段）
    - llm_response_raw         LLM 原始响应（截断 16KB 避免单行炸）
    - llm_parsed               _parse_response 解析后的 dict
    - llm_error                LLM 异常（None 表示无异常）
    - inputs_snapshot          入参三层 outlook 的精简快照（不含 narrative / evidence_refs）
    - final_decision           最终决策 dict
    """

    __tablename__ = "fact_decision_trace"
    __table_args__ = (
        UniqueConstraint(
            "business_date",
            "window_type",
            "decision_layer",
            "model_version",
            name="uq_fact_decision_trace_date_window_layer_model",
        ),
    )

    trace_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_date = Column(Date, nullable=False)
    window_type = Column(String(16), nullable=False)
    decision_layer = Column(String(32), nullable=False)
    market_regime = Column(String(32), nullable=True)
    verdict = Column(String(16), nullable=False)
    confidence = Column(Numeric(8, 4), nullable=False)
    risk_budget = Column(String(16), nullable=True)
    fallback_used = Column(Boolean, nullable=False, default=False)
    llm_error = Column(Text, nullable=True)
    model_version = Column(String(64), nullable=True)
    rule_baseline = Column(JSONB, nullable=False, default=dict)
    llm_response_raw = Column(Text, nullable=True)  # 截断 16KB
    llm_parsed = Column(JSONB, nullable=False, default=dict)
    inputs_snapshot = Column(JSONB, nullable=False, default=dict)
    final_decision = Column(JSONB, nullable=False, default=dict)
    rationale = Column(Text, nullable=True)
    # P5.3
    tenant_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)


class FactBacktestRun(Base):
    """回测 run 表 — 记录每次回测的入参、结果、状态（P2.5 / P2.3 持久化）。

    状态机：
    - pending    已提交，未开始
    - running    正在跑
    - completed  跑完（有 result_json）
    - failed     失败（有 error_message）
    - cancelled  主动取消

    唯一约束：(config_hash, business_date) — 同配置同日多次跑覆盖。
    唯一约束：job_id 单独 unique（用 UUID）。

    字段语义：
    - job_id           异步 job ID（UUID，async 模式必填；sync 模式可空）
    - status           状态枚举
    - config_hash      config 序列化 hash（SHA256 前 16 位）— 快速判同配置
    - config_json      完整 BacktestConfig JSON
    - result_json      完整 BacktestResult.to_dict() JSON（completed 时填）
    - error_message    失败原因（failed 时填）
    - cost_total_pct   累计交易成本（百分比）
    - benchmark_alpha  超额 alpha（年化，None=无 benchmark）
    - benchmark_beta   beta
    - attribution_json P4.5 归因分析结果（可选落库）
    """

    __tablename__ = "fact_backtest_run"
    __table_args__ = (
        UniqueConstraint(
            "config_hash",
            "business_date",
            name="uq_fact_backtest_run_config_date",
        ),
        UniqueConstraint(
            "job_id",
            name="uq_fact_backtest_run_job_id",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), nullable=True)
    business_date = Column(Date, nullable=False)
    status = Column(String(16), nullable=False, default="pending")
    config_hash = Column(String(32), nullable=False)
    config_json = Column(JSONB, nullable=False)
    result_json = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    cost_total_pct = Column(Numeric(12, 6), nullable=True)
    benchmark_alpha = Column(Numeric(12, 6), nullable=True)
    benchmark_beta = Column(Numeric(12, 6), nullable=True)
    attribution_json = Column(JSONB, nullable=True)
    # P5.3
    tenant_id = Column(String(64), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
