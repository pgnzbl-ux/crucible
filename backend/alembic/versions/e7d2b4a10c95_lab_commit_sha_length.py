"""labs.commit_sha 容纳 git SHA-256 / 上传包 sha256

Revision ID: e7d2b4a10c95
Revises: d4b7e1c08a92
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7d2b4a10c95"
down_revision: Union[str, None] = "d4b7e1c08a92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column(
        "labs",
        "commit_sha",
        existing_type=sa.String(length=40),
        type_=sa.String(length=64),
        existing_nullable=False,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.alter_column(
        "labs",
        "commit_sha",
        existing_type=sa.String(length=64),
        type_=sa.String(length=40),
        existing_nullable=False,
    )
