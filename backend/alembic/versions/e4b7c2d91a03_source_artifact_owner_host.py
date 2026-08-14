"""isolate source_artifacts by owner and git host

Revision ID: e4b7c2d91a03
Revises: c8f3a1e90b12
Create Date: 2026-08-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b7c2d91a03"
down_revision: Union[str, None] = "c8f3a1e90b12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("source_artifacts") as batch:
        batch.add_column(
            sa.Column(
                "owner_id",
                sa.String(length=36),
                nullable=False,
                server_default="",
                comment="任务所有者，缓存按用户隔离",
            )
        )
        batch.drop_constraint("uq_source_artifacts_key_ref", type_="unique")
        batch.create_unique_constraint(
            "uq_source_artifacts_owner_host_ref",
            ["owner_id", "git_host", "project_key", "ref_type", "ref_name"],
        )
        batch.create_index("ix_source_artifacts_owner_id", ["owner_id"])


def downgrade() -> None:
    with op.batch_alter_table("source_artifacts") as batch:
        batch.drop_index("ix_source_artifacts_owner_id")
        batch.drop_constraint("uq_source_artifacts_owner_host_ref", type_="unique")
        batch.create_unique_constraint(
            "uq_source_artifacts_key_ref",
            ["project_key", "ref_type", "ref_name"],
        )
        batch.drop_column("owner_id")
