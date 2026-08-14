"""add source_artifacts table for minio git cache

Revision ID: c8f3a1e90b12
Revises: d2d584fe83eb
Create Date: 2026-08-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8f3a1e90b12"
down_revision: Union[str, None] = "d2d584fe83eb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_artifacts",
        sa.Column("git_url", sa.String(length=1024), nullable=False, comment="用户提交地址（已去 .git 后缀）"),
        sa.Column("git_host", sa.String(length=255), nullable=False, comment="github.com 等"),
        sa.Column("project_key", sa.String(length=512), nullable=False, comment="space/project，如 siteboon/claudecodeui"),
        sa.Column("repo_dirname", sa.String(length=255), nullable=False, comment="落地目录名"),
        sa.Column("ref_type", sa.String(length=16), nullable=False, comment="branch|tag|commit"),
        sa.Column("ref_name", sa.String(length=255), nullable=False, comment="main / v1.0.0 / sha"),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("bucket", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("object_url", sa.String(length=1024), nullable=False, comment="MinIO 访问地址"),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_key", "ref_type", "ref_name", name="uq_source_artifacts_key_ref"),
    )
    op.create_index(op.f("ix_source_artifacts_project_key"), "source_artifacts", ["project_key"], unique=False)
    op.create_index(op.f("ix_source_artifacts_commit_sha"), "source_artifacts", ["commit_sha"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_source_artifacts_commit_sha"), table_name="source_artifacts")
    op.drop_index(op.f("ix_source_artifacts_project_key"), table_name="source_artifacts")
    op.drop_table("source_artifacts")
