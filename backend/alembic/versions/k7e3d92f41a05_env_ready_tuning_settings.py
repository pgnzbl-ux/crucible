"""env_ready tuning settings

Revision ID: k7e3d92f41a05
Revises: w8d4f2a61b73
Create Date: 2026-08-28

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "k7e3d92f41a05"
down_revision: Union[str, None] = "w8d4f2a61b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("platform_settings")}
    additions = (
        ("env_ready_max_attempts", 5, "靶场搭建 AI 排障轮数上限"),
        ("env_ready_compose_up_timeout_seconds", 600, "单轮 docker compose up 硬超时(秒)"),
        ("env_ready_compose_wait_seconds", 300, "compose 等待容器 healthy 上限(秒)；重应用建议调大"),
        ("env_ready_lab_wait_timeout_seconds", 1860, "等待共享靶场就绪上限(秒)"),
        ("env_ready_probe_window_seconds", 90, "compose up 后应用探活窗口(秒)"),
    )
    for name, default, comment in additions:
        if name not in columns:
            op.add_column(
                "platform_settings",
                sa.Column(
                    name,
                    sa.Integer(),
                    nullable=False,
                    server_default=str(default),
                    comment=comment,
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("platform_settings")}
    for name in (
        "env_ready_probe_window_seconds",
        "env_ready_lab_wait_timeout_seconds",
        "env_ready_compose_wait_seconds",
        "env_ready_compose_up_timeout_seconds",
        "env_ready_max_attempts",
    ):
        if name in columns:
            op.drop_column("platform_settings", name)
