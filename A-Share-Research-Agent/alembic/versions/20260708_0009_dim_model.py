"""dim_model 表迁移 — P2-T7。

Revision ID: 20260708_0009
Revises: 20260708_0008
Create Date: 2026-07-08 16:00:00

- dim_model: 模型注册表（配置驱动 A/B 的载体）
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "20260708_0009"
down_revision: Union[str, None] = "20260708_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dim_model",
        sa.Column("model_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("model_provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("weight", sa.Numeric(6, 4), nullable=False, server_default="1.0"),
        sa.Column("config_json", JSONB, nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("role", "version", name="uq_dim_model_role_version"),
    )
    op.create_index("ix_dim_model_role", "dim_model", ["role"], unique=False)
    op.create_index("ix_dim_model_is_active", "dim_model", ["is_active"], unique=False)
    op.create_index("ix_dim_model_tenant", "dim_model", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_dim_model_tenant", table_name="dim_model")
    op.drop_index("ix_dim_model_is_active", table_name="dim_model")
    op.drop_index("ix_dim_model_role", table_name="dim_model")
    op.drop_table("dim_model")
