"""projects source_type and uploaded artifact sha256

Revision ID: d4b7e1c08a92
Revises: f3a9c2d18e04
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4b7e1c08a92"
down_revision: Union[str, None] = "f3a9c2d18e04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    project_cols = {c["name"] for c in sa.inspect(bind).get_columns("projects")}
    if "source_type" not in project_cols:
        op.add_column(
            "projects",
            sa.Column(
                "source_type",
                sa.String(length=20),
                nullable=False,
                server_default="git",
            ),
        )
    if bind.dialect.name == "postgresql":
        op.alter_column(
            "source_artifacts",
            "commit_sha",
            existing_type=sa.String(length=40),
            type_=sa.String(length=64),
            existing_nullable=False,
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "source_artifacts",
            "commit_sha",
            existing_type=sa.String(length=64),
            type_=sa.String(length=40),
            existing_nullable=False,
        )
    op.drop_column("projects", "source_type")
