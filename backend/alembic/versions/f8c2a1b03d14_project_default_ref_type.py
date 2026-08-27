"""projects.default_ref_type 登记 Git 默认引用类型

Revision ID: f8c2a1b03d14
Revises: e7d2b4a10c95
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8c2a1b03d14"
down_revision: Union[str, None] = "e7d2b4a10c95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    project_cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("projects")}
    if "default_ref_type" not in project_cols:
        op.add_column(
            "projects",
            sa.Column(
                "default_ref_type",
                sa.String(length=16),
                nullable=True,
                comment="branch|tag|commit",
            ),
        )


def downgrade() -> None:
    op.drop_column("projects", "default_ref_type")
