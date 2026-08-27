"""triage verdict provenance and family key

Revision ID: l5f8d2c31a70
Revises: k4f7b8c29d53
Create Date: 2026-08-23

级联收敛管线溯源：alert_groups 增加 verdict_source（判决来自哪一层）
与 family_key（同根因族，代表审议后族内传播）。

baseline 用当前 ORM 元数据建表（新库已含这些列），故增量迁移按项目
惯例做存在性检查幂等添加。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "l5f8d2c31a70"
down_revision: Union[str, None] = "k4f7b8c29d53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    columns = {c["name"] for c in insp.get_columns("alert_groups")}
    if "verdict_source" not in columns:
        op.add_column(
            "alert_groups",
            sa.Column(
                "verdict_source", sa.String(20), nullable=True,
                comment="agent | fast_model | rule | carryover | propagated；null=历史 agent 亲审",
            ),
        )
    if "family_key" not in columns:
        op.add_column(
            "alert_groups",
            sa.Column(
                "family_key", sa.String(64), nullable=True,
                comment="同根因族键(rule|cwe|module 哈希)",
            ),
        )
    indexes = {i["name"] for i in insp.get_indexes("alert_groups")}
    if "ix_alert_groups_family_key" not in indexes:
        op.create_index("ix_alert_groups_family_key", "alert_groups", ["family_key"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    indexes = {i["name"] for i in insp.get_indexes("alert_groups")}
    if "ix_alert_groups_family_key" in indexes:
        op.drop_index("ix_alert_groups_family_key", table_name="alert_groups")
    columns = {c["name"] for c in insp.get_columns("alert_groups")}
    if "family_key" in columns:
        op.drop_column("alert_groups", "family_key")
    if "verdict_source" in columns:
        op.drop_column("alert_groups", "verdict_source")
