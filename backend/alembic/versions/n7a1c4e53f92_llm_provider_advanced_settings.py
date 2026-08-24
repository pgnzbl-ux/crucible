"""llm provider advanced settings

Revision ID: n7a1c4e53f92
Revises: m6e0b3c42d81
Create Date: 2026-08-23

LLM Provider 全局高级设置：temperature / max_context_tokens / effort。
旧行回填默认 0.2 / 200000 / high。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "n7a1c4e53f92"
down_revision: Union[str, None] = "m6e0b3c42d81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_TEMPERATURE = "0.2"
_DEFAULT_MAX_CONTEXT = "200000"
_DEFAULT_EFFORT = "high"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("llm_providers")}
    if "temperature" not in columns:
        op.add_column(
            "llm_providers",
            sa.Column(
                "temperature",
                sa.Float(),
                nullable=False,
                server_default=_DEFAULT_TEMPERATURE,
                comment="采样温度 0–2；Messages API 全局约束（Agent CLI 暂不透传）",
            ),
        )
    if "max_context_tokens" not in columns:
        op.add_column(
            "llm_providers",
            sa.Column(
                "max_context_tokens",
                sa.Integer(),
                nullable=False,
                server_default=_DEFAULT_MAX_CONTEXT,
                comment="模型上下文窗口；注入 CLAUDE_CODE_MAX_CONTEXT_TOKENS 驱动 CLI 压缩",
            ),
        )
    if "effort" not in columns:
        op.add_column(
            "llm_providers",
            sa.Column(
                "effort",
                sa.String(20),
                nullable=False,
                server_default=_DEFAULT_EFFORT,
                comment="思考强度 low|medium|high|xhigh|max|auto",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("llm_providers")}
    if "effort" in columns:
        op.drop_column("llm_providers", "effort")
    if "max_context_tokens" in columns:
        op.drop_column("llm_providers", "max_context_tokens")
    if "temperature" in columns:
        op.drop_column("llm_providers", "temperature")
