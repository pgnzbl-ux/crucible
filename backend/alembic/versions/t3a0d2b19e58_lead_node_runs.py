"""persistent audit/reproduce runs per LeadRun

Revision ID: t3a0d2b19e58
Revises: s2f9c1a08e47
Create Date: 2026-08-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "t3a0d2b19e58"
down_revision: Union[str, None] = "s2f9c1a08e47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "lead_node_runs" in tables:
        return
    op.create_table(
        "lead_node_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "lead_run_id", sa.String(length=36),
            sa.ForeignKey("lead_runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=False),
        sa.Column("run_id", sa.String(length=36), sa.ForeignKey("task_runs.id"), nullable=False),
        sa.Column("node_key", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "lead_run_id", "node_key", "attempt",
            name="uq_lead_node_runs_lead_node_attempt",
        ),
    )
    op.create_index("ix_lead_node_runs_lead_run_id", "lead_node_runs", ["lead_run_id"])
    op.create_index("ix_lead_node_runs_task_id", "lead_node_runs", ["task_id"])
    op.create_index("ix_lead_node_runs_run_id", "lead_node_runs", ["run_id"])
    op.create_index("idx_lead_node_runs_task_status", "lead_node_runs", ["task_id", "status"])
    op.create_index("idx_lead_node_runs_lead_node", "lead_node_runs", ["lead_run_id", "node_key"])


def downgrade() -> None:
    if "lead_node_runs" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("lead_node_runs")
