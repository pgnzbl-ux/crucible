"""lead_runs table for discovery verify queue

Revision ID: j3e6a7b18c42
Revises: i2d5f6a07b31
Create Date: 2026-08-21

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j3e6a7b18c42"
down_revision: Union[str, None] = "i2d5f6a07b31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "lead_runs" not in tables:
        op.create_table(
            "lead_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=False),
            sa.Column("run_id", sa.String(length=36), sa.ForeignKey("task_runs.id"), nullable=False),
            sa.Column(
                "alert_group_id", sa.String(length=36),
                sa.ForeignKey("alert_groups.id"), nullable=False,
            ),
            sa.Column("queue_position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lead_description", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
            sa.Column("verdict", sa.String(length=30), nullable=True),
            sa.Column("gate_verdict", sa.String(length=20), nullable=True),
            sa.Column("audit_output", sa.JSON(), nullable=True),
            sa.Column("reproduce_output", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.UniqueConstraint("run_id", "alert_group_id", name="uq_lead_runs_run_group"),
        )
        op.create_index("idx_lead_runs_task_status", "lead_runs", ["task_id", "status"])
        op.create_index("ix_lead_runs_task_id", "lead_runs", ["task_id"])
        op.create_index("ix_lead_runs_run_id", "lead_runs", ["run_id"])
        op.create_index("ix_lead_runs_alert_group_id", "lead_runs", ["alert_group_id"])


def downgrade() -> None:
    op.drop_table("lead_runs")
