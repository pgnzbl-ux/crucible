"""store profile json on source_artifacts by commit sha

Revision ID: a7c4e18f2b90
Revises: e4b7c2d91a03
Create Date: 2026-08-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7c4e18f2b90"
down_revision: Union[str, None] = "e4b7c2d91a03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("source_artifacts") as batch:
        batch.add_column(
            sa.Column(
                "profile_json",
                sa.Text(),
                nullable=True,
                comment="该 commit 的画像 JSON；commit_sha 变更时清空",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("source_artifacts") as batch:
        batch.drop_column("profile_json")
