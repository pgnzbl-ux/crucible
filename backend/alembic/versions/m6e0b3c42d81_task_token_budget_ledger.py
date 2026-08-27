"""task token budget and usage ledger

Revision ID: m6e0b3c42d81
Revises: l5f8d2c31a70
Create Date: 2026-08-23

任务级 token 预算：agent_usage 台账表 + platform_settings.task_token_budget。
baseline 用当前 ORM 元数据建表（新库已含），增量迁移按项目惯例做
存在性检查幂等添加。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "m6e0b3c42d81"
down_revision: Union[str, None] = "l5f8d2c31a70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("platform_settings")}
    if "task_token_budget" not in columns:
        op.add_column(
            "platform_settings",
            sa.Column(
                "task_token_budget", sa.Integer(), nullable=False,
                server_default="0",
                comment="单任务 token 预算(prompt+completion)；0=不限。软停：耗尽后不开新 agent 会话",
            ),
        )
    if "agent_usage" not in insp.get_table_names():
        op.create_table(
            "agent_usage",
            sa.Column("task_id", sa.String(36), nullable=False),
            sa.Column("run_id", sa.String(36), nullable=True),
            sa.Column("node_key", sa.String(20), nullable=False),
            sa.Column("source", sa.String(20), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        )
        op.create_index("ix_agent_usage_task_id", "agent_usage", ["task_id"])
        op.create_index("ix_agent_usage_run_id", "agent_usage", ["run_id"])
        op.create_index("idx_agent_usage_task_node", "agent_usage", ["task_id", "node_key"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "agent_usage" in insp.get_table_names():
        op.drop_table("agent_usage")
    columns = {c["name"] for c in insp.get_columns("platform_settings")}
    if "task_token_budget" in columns:
        op.drop_column("platform_settings", "task_token_budget")
