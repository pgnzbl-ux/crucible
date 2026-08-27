"""task time budget and ai node timeout settings

Revision ID: u4b1e7c30a91
Revises: t3a0d2b19e58
Create Date: 2026-08-27

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "u4b1e7c30a91"
down_revision: Union[str, None] = "t3a0d2b19e58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("platform_settings")}
    additions = (
        ("task_time_budget_seconds", 10800, "单任务总时长预算(秒)；0=不限。生效值取本预算与 Celery 软限较小者"),
        ("ai_node_timeout_seconds", 3600, "单 AI 节点最长执行秒数(获槽后起计)；0=不限。超时杀容器，节点按失败收尾"),
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
    for name in ("ai_node_timeout_seconds", "task_time_budget_seconds"):
        if name in columns:
            op.drop_column("platform_settings", name)
