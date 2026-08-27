"""discovery WP1: task_type/溯源指针 + scan_runs + finding 表 + provider role

discovery-spec §5。tasks.source_alert_group_id 无物理 FK(跨 Context 逻辑指针)；
tasks.vulnerability_description 改 nullable(discovery 任务为空)。

Revision ID: i2d5f6a07b31
Revises: h1c4d8e05f26
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i2d5f6a07b31"
down_revision: Union[str, None] = "h1c4d8e05f26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bind():
    return op.get_bind()


def upgrade() -> None:
    bind = _bind()
    inspector = sa.inspect(bind)

    # --- tasks: task_type / source_alert_group_id / description nullable ---
    task_cols = {c["name"] for c in inspector.get_columns("tasks")}
    if "task_type" not in task_cols:
        op.add_column(
            "tasks",
            sa.Column("task_type", sa.String(length=20), nullable=False,
                      server_default="verify", comment="verify(漏洞验证) | discovery(仓库审计)"),
        )
    if "source_alert_group_id" not in task_cols:
        op.add_column(
            "tasks",
            sa.Column("source_alert_group_id", sa.String(length=36), nullable=True,
                      comment="来源 AlertGroup id(发现侧→验证侧溯源)"),
        )
        op.create_index("ix_tasks_source_alert_group_id", "tasks", ["source_alert_group_id"])
    if "vulnerability_description" in task_cols:
        with op.batch_alter_table("tasks") as batch:
            batch.alter_column("vulnerability_description", existing_type=sa.Text(), nullable=True)

    # --- llm_providers.role(部分唯一索引：role 非空时唯一) ---
    provider_cols = {c["name"] for c in inspector.get_columns("llm_providers")}
    if "role" not in provider_cols:
        op.add_column(
            "llm_providers",
            sa.Column("role", sa.String(length=20), nullable=True, comment="模型角色；空=不占角色"),
        )
    existing_idx = {i["name"] for i in inspector.get_indexes("llm_providers")}
    if "idx_llm_providers_role" not in existing_idx:
        if bind.dialect.name == "postgresql":
            op.create_index(
                "idx_llm_providers_role", "llm_providers", ["role"],
                unique=True, postgresql_where=sa.text("role IS NOT NULL"),
            )
        else:
            op.create_index("idx_llm_providers_role", "llm_providers", ["role"])

    # --- discovery: scan_runs ---
    if "scan_runs" not in inspector.get_table_names():
        op.create_table(
            "scan_runs",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=False),
            sa.Column("run_id", sa.String(length=36), sa.ForeignKey("task_runs.id"), nullable=False),
            sa.Column("node_run_id", sa.String(length=36), nullable=False),
            sa.Column("engine", sa.String(length=20), nullable=False,
                      comment="semgrep | gitleaks | osv"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
            sa.Column("config_summary", sa.JSON(), nullable=False,
                      server_default="{}"),
            sa.Column("finding_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sarif_key", sa.String(length=500), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_scan_runs_task_id", "scan_runs", ["task_id"])
        op.create_index("ix_scan_runs_run_id", "scan_runs", ["run_id"])
        op.create_index("ix_scan_runs_node_run_id", "scan_runs", ["node_run_id"])

    # --- finding: raw_findings / alert_groups / adjudications / review_actions ---
    tables = inspector.get_table_names()
    if "raw_findings" not in tables:
        op.create_table(
            "raw_findings",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=False),
            sa.Column("scan_run_id", sa.String(length=36), sa.ForeignKey("scan_runs.id"), nullable=False),
            sa.Column("engine", sa.String(length=20), nullable=False),
            sa.Column("rule_id", sa.String(length=255), nullable=False),
            sa.Column("cwe", sa.String(length=20), nullable=True),
            sa.Column("severity", sa.String(length=20), nullable=True),
            sa.Column("file_path", sa.String(length=1024), nullable=False),
            sa.Column("line_start", sa.Integer(), nullable=True),
            sa.Column("line_end", sa.Integer(), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("source_to_sink", sa.JSON(), nullable=True),
            sa.Column("code_snippet", sa.Text(), nullable=True),
            sa.Column("fingerprint", sa.String(length=64), nullable=False),
            sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("task_id", "fingerprint", name="uq_raw_findings_task_fp"),
        )
        op.create_index("ix_raw_findings_task_id", "raw_findings", ["task_id"])
        op.create_index("idx_raw_findings_task_engine", "raw_findings", ["task_id", "engine"])
        op.create_index("ix_raw_findings_scan_run_id", "raw_findings", ["scan_run_id"])

    if "alert_groups" not in tables:
        op.create_table(
            "alert_groups",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("task_id", sa.String(length=36), sa.ForeignKey("tasks.id"), nullable=False),
            sa.Column("group_key", sa.String(length=64), nullable=False),
            sa.Column("cwe", sa.String(length=20), nullable=True),
            sa.Column("file_path", sa.String(length=1024), nullable=False),
            sa.Column("function_symbol", sa.String(length=255), nullable=True),
            sa.Column("line_span", sa.String(length=32), nullable=True),
            sa.Column("member_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("representative_finding_id", sa.String(length=36),
                      sa.ForeignKey("raw_findings.id"), nullable=False),
            sa.Column("engine_set", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="new"),
            sa.Column("clue_grade", sa.String(length=2), nullable=True),
            sa.Column("ai_verdict", sa.String(length=20), nullable=True),
            sa.Column("ai_confidence", sa.Float(), nullable=True),
            sa.Column("priority", sa.String(length=10), nullable=True),
            sa.Column("resolution", sa.String(length=20), nullable=True),
            sa.UniqueConstraint("task_id", "group_key", name="uq_alert_groups_task_key"),
        )
        op.create_index("ix_alert_groups_task_id", "alert_groups", ["task_id"])
        op.create_index("idx_alert_groups_task_status", "alert_groups", ["task_id", "status"])

    if "adjudications" not in tables:
        op.create_table(
            "adjudications",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("alert_group_id", sa.String(length=36), sa.ForeignKey("alert_groups.id"), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("provider_id", sa.String(length=36), sa.ForeignKey("llm_providers.id"), nullable=True),
            sa.Column("model", sa.String(length=120), nullable=True),
            sa.Column("verdict", sa.String(length=20), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("why", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("evidence", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("need", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("context_log", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("prompt_text", sa.Text(), nullable=False),
            sa.Column("response_text", sa.Text(), nullable=False),
            sa.Column("usage", sa.JSON(), nullable=False, server_default="{}"),
        )
        op.create_index("idx_adjudications_group", "adjudications", ["alert_group_id", "attempt"])

    if "review_actions" not in tables:
        op.create_table(
            "review_actions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("alert_group_id", sa.String(length=36), sa.ForeignKey("alert_groups.id"), nullable=False),
            sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("reason_tags", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("reason_text", sa.Text(), nullable=True),
        )
        op.create_index("idx_review_actions_group", "review_actions", ["alert_group_id"])
        op.create_index("ix_review_actions_user_id", "review_actions", ["user_id"])


def downgrade() -> None:
    op.drop_table("review_actions")
    op.drop_table("adjudications")
    op.drop_table("alert_groups")
    op.drop_table("raw_findings")
    op.drop_table("scan_runs")
    op.drop_index("idx_llm_providers_role", table_name="llm_providers")
    op.drop_column("llm_providers", "role")
    op.drop_index("ix_tasks_source_alert_group_id", table_name="tasks")
    op.drop_column("tasks", "source_alert_group_id")
    op.drop_column("tasks", "task_type")
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column("vulnerability_description", existing_type=sa.Text(), nullable=False)
