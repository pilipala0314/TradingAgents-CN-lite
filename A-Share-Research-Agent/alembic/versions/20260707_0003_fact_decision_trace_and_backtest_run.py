"""add fact_decision_trace + fact_backtest_run tables

Revision ID: 20260707_0003
Revises: 20260701_0002
Create Date: 2026-07-07 14:30:00

P2.4 / P2.5 / P4.4 阶段新增：

  - fact_decision_trace   决策 trace（LLM 原始 + 规则 baseline + 异常）
  - fact_backtest_run     回测 run 落库（异步 job + 状态机 + 归因）

均带 unique 约束，支持同 (date, ...) 重复跑自动覆盖。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260707_0003"
down_revision: Union[str, None] = "20260701_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) fact_decision_trace — 决策 trace（P4.4）
    op.create_table(
        "fact_decision_trace",
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("window_type", sa.String(length=16), nullable=False),
        sa.Column("decision_layer", sa.String(length=32), nullable=False),
        sa.Column("market_regime", sa.String(length=32), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("risk_budget", sa.String(length=16), nullable=True),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("llm_error", sa.Text(), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("rule_baseline", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm_response_raw", sa.Text(), nullable=True),
        sa.Column("llm_parsed", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inputs_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("final_decision", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("trace_id", name="pk_fact_decision_trace"),
        sa.UniqueConstraint(
            "business_date",
            "window_type",
            "decision_layer",
            "model_version",
            name="uq_fact_decision_trace_date_window_layer_model",
        ),
    )
    op.create_index(
        op.f("ix_fact_decision_trace_business_date"),
        "fact_decision_trace",
        ["business_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fact_decision_trace_decision_layer"),
        "fact_decision_trace",
        ["decision_layer"],
        unique=False,
    )

    # 2) fact_backtest_run — 回测 run 落库（P2.5 / P2.3）
    op.create_table(
        "fact_backtest_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config_hash", sa.String(length=32), nullable=False),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cost_total_pct", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("benchmark_alpha", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("benchmark_beta", sa.Numeric(precision=12, scale=6), nullable=True),
        sa.Column("attribution_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_fact_backtest_run"),
        sa.UniqueConstraint(
            "config_hash",
            "business_date",
            name="uq_fact_backtest_run_config_date",
        ),
        sa.UniqueConstraint("job_id", name="uq_fact_backtest_run_job_id"),
    )
    op.create_index(
        op.f("ix_fact_backtest_run_business_date"),
        "fact_backtest_run",
        ["business_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fact_backtest_run_status"),
        "fact_backtest_run",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fact_backtest_run_config_hash"),
        "fact_backtest_run",
        ["config_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_fact_backtest_run_config_hash"), table_name="fact_backtest_run")
    op.drop_index(op.f("ix_fact_backtest_run_status"), table_name="fact_backtest_run")
    op.drop_index(op.f("ix_fact_backtest_run_business_date"), table_name="fact_backtest_run")
    op.drop_table("fact_backtest_run")
    op.drop_index(op.f("ix_fact_decision_trace_decision_layer"), table_name="fact_decision_trace")
    op.drop_index(op.f("ix_fact_decision_trace_business_date"), table_name="fact_decision_trace")
    op.drop_table("fact_decision_trace")
