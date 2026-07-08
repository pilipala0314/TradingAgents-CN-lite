"""fact_strategy_config 表迁移 — P2-T11。

Revision ID: 20260708_0010
Revises: 20260708_0009
Create Date: 2026-07-08 20:30:00

- fact_strategy_config: 当前生效 strategy 配置（手动切换 / 自动最优）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "20260708_0010"
down_revision: Union[str, None] = "20260708_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fact_strategy_config",
        sa.Column("config_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("strategy_name", sa.String(length=128), nullable=False),
        sa.Column("universe_code", sa.String(length=64), nullable=True),
        sa.Column("params_json", JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("selection_mode", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("metrics_snapshot", JSONB, nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("selected_by", sa.String(length=64), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fact_strategy_config_run_id", "fact_strategy_config", ["run_id"], unique=False)
    op.create_index("ix_fact_strategy_config_strategy", "fact_strategy_config", ["strategy_name"], unique=False)
    op.create_index("ix_fact_strategy_config_active", "fact_strategy_config", ["is_active"], unique=False)
    op.create_index("ix_fact_strategy_config_tenant", "fact_strategy_config", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_fact_strategy_config_tenant", table_name="fact_strategy_config")
    op.drop_index("ix_fact_strategy_config_active", table_name="fact_strategy_config")
    op.drop_index("ix_fact_strategy_config_strategy", table_name="fact_strategy_config")
    op.drop_index("ix_fact_strategy_config_run_id", table_name="fact_strategy_config")
    op.drop_table("fact_strategy_config")
