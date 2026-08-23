"""runtime concurrency and resource budget

Revision ID: k4f7b8c29d53
Revises: j3e6a7b18c42
Create Date: 2026-08-22

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "k4f7b8c29d53"
down_revision: Union[str, None] = "j3e6a7b18c42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("platform_settings")}
    additions = (
        ("max_concurrent_agent_runners", 4, "全平台同时运行的 AI 容器软上限"),
        ("lead_verify_per_task", 2, "单任务同时终认的线索数"),
        ("reproduce_per_lab", 1, "同一靶场同时执行的复现数"),
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
        "reproduce_per_lab",
        "lead_verify_per_task",
        "max_concurrent_agent_runners",
    ):
        if name in columns:
            op.drop_column("platform_settings", name)
