"""report product fields

Revision ID: w8d4f2a61b73
Revises: u4b1e7c30a91
Create Date: 2026-08-27

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "w8d4f2a61b73"
down_revision: Union[str, None] = "u4b1e7c30a91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("reports")}
    additions = (
        ("product_name", "产品名称（本地上传任务=项目名）"),
        ("affected_version", "影响版本 ref@commit（本次复现版本）"),
        ("project_address", "项目地址（git 地址；本地上传=项目名）"),
    )
    for name, comment in additions:
        if name not in columns:
            op.add_column(
                "reports",
                sa.Column(name, sa.String(512 if name == "project_address" else 255), nullable=True, comment=comment),
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("reports")}
    for name in ("project_address", "affected_version", "product_name"):
        if name in columns:
            op.drop_column("reports", name)
