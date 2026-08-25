"""health audit: encrypted secrets, membership, indexes

Revision ID: r1e8a0b97d36
Revises: q0d4f7e86c25
Create Date: 2026-08-25

- raw_findings.alert_group_id（组成员指针）
- 部分唯一 is_default
- 查询索引
- 存量 LLM/凭据明文回填 Fernet
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "r1e8a0b97d36"
down_revision: Union[str, None] = "q0d4f7e86c25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_index(insp, table: str, name: str, columns: list[str], **kw) -> None:
    if table not in insp.get_table_names():
        return
    existing = {i["name"] for i in insp.get_indexes(table)}
    if name not in existing:
        op.create_index(name, table, columns, **kw)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "raw_findings" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("raw_findings")}
        if "alert_group_id" not in cols:
            op.add_column(
                "raw_findings",
                sa.Column("alert_group_id", sa.String(length=36), nullable=True),
            )
        _ensure_index(insp, "raw_findings", "ix_raw_findings_alert_group_id", ["alert_group_id"])
        _ensure_index(insp, "raw_findings", "ix_raw_findings_scan_run_id", ["scan_run_id"])
        if bind.dialect.name == "postgresql":
            fks = {fk["name"] for fk in insp.get_foreign_keys("raw_findings")}
            if "fk_raw_findings_alert_group_id" not in fks:
                op.create_foreign_key(
                    "fk_raw_findings_alert_group_id",
                    "raw_findings",
                    "alert_groups",
                    ["alert_group_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    _ensure_index(insp, "alert_groups", "idx_alert_groups_updated_at", ["updated_at"])
    _ensure_index(insp, "review_actions", "ix_review_actions_user_id", ["user_id"])
    _ensure_index(insp, "adjudications", "ix_adjudications_provider_id", ["provider_id"])
    _ensure_index(insp, "labs", "ix_labs_project_id", ["project_id"])
    _ensure_index(insp, "labs", "ix_labs_status", ["status"])
    _ensure_index(insp, "tasks", "ix_tasks_task_type", ["task_type"])

    if "llm_providers" in insp.get_table_names():
        existing = {i["name"] for i in insp.get_indexes("llm_providers")}
        if "uq_llm_providers_one_default" not in existing:
            if bind.dialect.name == "postgresql":
                op.create_index(
                    "uq_llm_providers_one_default",
                    "llm_providers",
                    ["is_default"],
                    unique=True,
                    postgresql_where=sa.text("is_default IS TRUE"),
                )
            else:
                op.create_index(
                    "uq_llm_providers_one_default",
                    "llm_providers",
                    ["is_default"],
                    unique=True,
                    sqlite_where=sa.text("is_default = 1"),
                )

    _backfill_sealed_secrets(bind)

    if bind.dialect.name == "postgresql":
        if "llm_providers" in insp.get_table_names():
            op.alter_column(
                "llm_providers",
                "api_key_encrypted",
                existing_type=sa.Text(),
                comment="Fernet 密文 API Key(响应层掩码；存量明文可读)",
                existing_nullable=True,
            )
        if "credentials" in insp.get_table_names():
            op.alter_column(
                "credentials",
                "secret_encrypted",
                existing_type=sa.Text(),
                comment="Fernet 密文凭据值(响应层掩码；存量明文可读)",
                existing_nullable=True,
            )


def _backfill_sealed_secrets(bind) -> None:
    from app.core.crypto import seal_secret

    insp = sa.inspect(bind)
    for table, column in (
        ("llm_providers", "api_key_encrypted"),
        ("credentials", "secret_encrypted"),
    ):
        if table not in insp.get_table_names():
            continue
        rows = bind.execute(sa.text(f"SELECT id, {column} FROM {table}")).fetchall()
        for row_id, stored in rows:
            if not stored or str(stored).startswith("gAAAAA"):
                continue
            sealed = seal_secret(str(stored))
            bind.execute(
                sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :id"),
                {"v": sealed, "id": row_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "llm_providers" in insp.get_table_names():
        existing = {i["name"] for i in insp.get_indexes("llm_providers")}
        if "uq_llm_providers_one_default" in existing:
            op.drop_index("uq_llm_providers_one_default", table_name="llm_providers")
    for table, name in (
        ("tasks", "ix_tasks_task_type"),
        ("labs", "ix_labs_status"),
        ("labs", "ix_labs_project_id"),
        ("adjudications", "ix_adjudications_provider_id"),
        ("review_actions", "ix_review_actions_user_id"),
        ("alert_groups", "idx_alert_groups_updated_at"),
        ("raw_findings", "ix_raw_findings_alert_group_id"),
    ):
        if table in insp.get_table_names():
            names = {i["name"] for i in insp.get_indexes(table)}
            if name in names:
                op.drop_index(name, table_name=table)
    if "raw_findings" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("raw_findings")}
        if "alert_group_id" in cols:
            if bind.dialect.name == "postgresql":
                fks = {fk["name"] for fk in insp.get_foreign_keys("raw_findings")}
                if "fk_raw_findings_alert_group_id" in fks:
                    op.drop_constraint(
                        "fk_raw_findings_alert_group_id",
                        "raw_findings",
                        type_="foreignkey",
                    )
            op.drop_column("raw_findings", "alert_group_id")
