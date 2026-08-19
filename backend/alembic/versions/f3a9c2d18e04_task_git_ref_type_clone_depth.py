"""task git ref type and clone depth

Revision ID: f3a9c2d18e04
Revises: e8c3a1b047d2
Create Date: 2026-08-19
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3a9c2d18e04"
down_revision: Union[str, None] = "e8c3a1b047d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("project_ref_type", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "clone_depth",
            sa.Integer(),
            nullable=True,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "clone_depth")
    op.drop_column("tasks", "project_ref_type")
