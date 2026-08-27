"""llm provider explicit auth mode

Revision ID: o8b2d5c64a03
Revises: n7a1c4e53f92
Create Date: 2026-08-24

新增显式认证方式，避免 Agent、轻量 Messages 和连接测试同时或分别使用
不同认证头。现有 custom/deepseek 保持历史 Bearer 行为；Anthropic 回填 API Key。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "o8b2d5c64a03"
down_revision: Union[str, None] = "n7a1c4e53f92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("llm_providers")}
    if "auth_mode" not in columns:
        op.add_column(
            "llm_providers",
            sa.Column(
                "auth_mode",
                sa.String(20),
                nullable=False,
                server_default="bearer",
                comment="认证方式 api_key(X-Api-Key) | bearer(Authorization)",
            ),
        )
    op.execute(
        sa.text(
            "UPDATE llm_providers SET auth_mode = 'api_key' "
            "WHERE provider_type = 'anthropic'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("llm_providers")}
    if "auth_mode" in columns:
        op.drop_column("llm_providers", "auth_mode")
