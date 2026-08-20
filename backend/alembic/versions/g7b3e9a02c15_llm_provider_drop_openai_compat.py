"""llm_providers：移除 openai_compat，统一为 Anthropic 兼容预设

Revision ID: g7b3e9a02c15
Revises: f8c2a1b03d14
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g7b3e9a02c15"
down_revision: Union[str, None] = "f8c2a1b03d14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE llm_providers SET provider_type = 'custom' "
            "WHERE provider_type = 'openai_compat'"
        )
    )


def downgrade() -> None:
    pass
