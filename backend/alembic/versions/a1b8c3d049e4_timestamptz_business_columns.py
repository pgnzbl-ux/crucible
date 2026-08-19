"""timestamptz for business datetime columns written as aware UTC

Revision ID: a1b8c3d049e4
Revises: e8c3a1b047d2
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b8c3d049e4"
down_revision: Union[str, None] = "e8c3a1b047d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("task_runs", "started_at"),
    ("task_runs", "finished_at"),
    ("node_runs", "started_at"),
    ("node_runs", "finished_at"),
    ("reports", "published_at"),
    ("projects", "last_cloned_at"),
)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=False),
            type_=sa.DateTime(timezone=True),
            existing_nullable=True,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(timezone=False),
            existing_nullable=True,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
