"""add labs table and tasks.lab_id

Revision ID: 9f2d6c8a4b17
Revises: a7c4e18f2b90
Create Date: 2026-08-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9f2d6c8a4b17"
down_revision: Union[str, None] = "a7c4e18f2b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "labs",
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("compose_project", sa.String(length=255), nullable=False),
        sa.Column("workdir", sa.String(length=1024), nullable=False),
        sa.Column("target_url", sa.String(length=1024), nullable=True),
        sa.Column("compose_path", sa.String(length=1024), nullable=True),
        sa.Column("transport_shape", sa.Text(), nullable=False),
        sa.Column("initial_creds", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ttl_seconds", sa.Integer(), nullable=False),
        sa.Column("creator_task_id", sa.String(length=36), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "id",
            sa.String(length=36),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "project_id",
            "commit_sha",
            name="uq_labs_owner_project_sha",
        ),
    )
    with op.batch_alter_table("tasks") as batch:
        batch.add_column(
            sa.Column(
                "lab_id",
                sa.String(length=36),
                nullable=True,
                comment="共用靶场 Lab",
            )
        )
        batch.create_foreign_key(
            "fk_tasks_lab_id_labs",
            "labs",
            ["lab_id"],
            ["id"],
        )
        batch.create_index("ix_tasks_lab_id", ["lab_id"])


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch:
        batch.drop_index("ix_tasks_lab_id")
        batch.drop_constraint("fk_tasks_lab_id_labs", type_="foreignkey")
        batch.drop_column("lab_id")
    op.drop_table("labs")
