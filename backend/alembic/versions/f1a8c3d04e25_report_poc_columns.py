"""add report poc columns

Revision ID: f1a8c3d04e25
Revises: 9f2d6c8a4b17
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a8c3d04e25"
down_revision: Union[str, None] = "9f2d6c8a4b17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.add_column(sa.Column("poc_language", sa.String(length=16), nullable=True, comment="python/bash/other"))
        batch.add_column(sa.Column("poc_filename", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("poc_code", sa.Text(), nullable=True, comment="完整 PoC 源码"))
        batch.add_column(sa.Column("poc_usage", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("poc_usage")
        batch.drop_column("poc_code")
        batch.drop_column("poc_filename")
        batch.drop_column("poc_language")
