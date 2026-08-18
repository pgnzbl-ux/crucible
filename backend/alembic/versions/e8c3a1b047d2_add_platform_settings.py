"""add platform_settings singleton

Revision ID: e8c3a1b047d2
Revises: b7e4c2a19f08
Create Date: 2026-08-18
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8c3a1b047d2"
down_revision: Union[str, None] = "b7e4c2a19f08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("singleton_key", sa.String(length=20), nullable=False),
        sa.Column(
            "max_concurrent_tasks",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key"),
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
