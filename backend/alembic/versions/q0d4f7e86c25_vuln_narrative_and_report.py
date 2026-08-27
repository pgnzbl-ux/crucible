"""vuln narrative + verification_basis + vuln_report

Revision ID: q0d4f7e86c25
Revises: p9c3e6d75b14
Create Date: 2026-08-25

discovery-spec §2.3.1 / §11.1：Adjudication 叙事字段；LeadRun 验证方式；
AlertGroup 独立漏洞报告 JSON。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "q0d4f7e86c25"
down_revision: Union[str, None] = "p9c3e6d75b14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    if "adjudications" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("adjudications")}
        if "summary" not in cols:
            op.add_column(
                "adjudications",
                sa.Column("summary", sa.Text(), nullable=True, comment="1～3 句漏洞简述"),
            )
        if "reasoning" not in cols:
            op.add_column(
                "adjudications",
                sa.Column(
                    "reasoning",
                    sa.Text(),
                    nullable=True,
                    comment="代码/依赖推理（入口→路径→危险点）",
                ),
            )

    if "lead_runs" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("lead_runs")}
        if "verification_basis" not in cols:
            op.add_column(
                "lead_runs",
                sa.Column(
                    "verification_basis",
                    sa.String(20),
                    nullable=True,
                    comment="lab | code_path",
                ),
            )

    if "alert_groups" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("alert_groups")}
        if "verification_basis" not in cols:
            op.add_column(
                "alert_groups",
                sa.Column(
                    "verification_basis",
                    sa.String(20),
                    nullable=True,
                    comment="lab | code_path；与终认 LeadRun 对齐",
                ),
            )
        if "vuln_report" not in cols:
            op.add_column(
                "alert_groups",
                sa.Column(
                    "vuln_report",
                    json_type,
                    nullable=True,
                    comment="终局成功时一漏洞一份报告 JSON（§11.1）",
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "alert_groups" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("alert_groups")}
        if "vuln_report" in cols:
            op.drop_column("alert_groups", "vuln_report")
        if "verification_basis" in cols:
            op.drop_column("alert_groups", "verification_basis")

    if "lead_runs" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("lead_runs")}
        if "verification_basis" in cols:
            op.drop_column("lead_runs", "verification_basis")

    if "adjudications" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("adjudications")}
        if "reasoning" in cols:
            op.drop_column("adjudications", "reasoning")
        if "summary" in cols:
            op.drop_column("adjudications", "summary")
