"""agent_usage cache token columns

Revision ID: p9c3e6d75b14
Revises: o8b2d5c64a03
Create Date: 2026-08-25

SDK/API 回传的 cache_read / cache_creation 分项入台账，对齐厂商处理量口径。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "p9c3e6d75b14"
down_revision: Union[str, None] = "o8b2d5c64a03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "agent_usage" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("agent_usage")}
    if "cache_read_input_tokens" not in columns:
        op.add_column(
            "agent_usage",
            sa.Column(
                "cache_read_input_tokens",
                sa.Integer(),
                nullable=False,
                server_default="0",
                comment="SDK/API cache_read_input_tokens；禁止自算",
            ),
        )
    if "cache_creation_input_tokens" not in columns:
        op.add_column(
            "agent_usage",
            sa.Column(
                "cache_creation_input_tokens",
                sa.Integer(),
                nullable=False,
                server_default="0",
                comment="SDK/API cache_creation_input_tokens；禁止自算",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "agent_usage" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("agent_usage")}
    if "cache_creation_input_tokens" in columns:
        op.drop_column("agent_usage", "cache_creation_input_tokens")
    if "cache_read_input_tokens" in columns:
        op.drop_column("agent_usage", "cache_read_input_tokens")
