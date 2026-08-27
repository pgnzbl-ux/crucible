"""unique projects owner+name; unique adjudication attempt

Revision ID: s2f9c1a08e47
Revises: r1e8a0b97d36
Create Date: 2026-08-25

- projects (owner_id, name) 唯一（与 Service 409 对齐）
- adjudications (alert_group_id, attempt) 唯一
存量重复行时 Fail-Fast，禁止带病加约束。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "s2f9c1a08e47"
down_revision: Union[str, None] = "r1e8a0b97d36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_named_unique(insp, table: str, name: str) -> bool:
    if table not in insp.get_table_names():
        return True
    uniques = {u["name"] for u in insp.get_unique_constraints(table) if u.get("name")}
    indexes = {i["name"] for i in insp.get_indexes(table)}
    return name in uniques or name in indexes


def _require_no_duplicates(bind, sql: str, label: str) -> None:
    rows = bind.execute(sa.text(sql)).fetchall()
    if rows:
        detail = ", ".join("/".join(str(x) for x in row) for row in rows[:20])
        raise RuntimeError(f"{label} 存在重复行，无法加唯一约束: {detail}")


def _ensure_unique_index(insp, table: str, name: str, columns: list[str]) -> None:
    if _has_named_unique(insp, table, name):
        return
    op.create_index(name, table, columns, unique=True)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "projects" in insp.get_table_names() and not _has_named_unique(
        insp, "projects", "uq_projects_owner_name",
    ):
        _require_no_duplicates(
            bind,
            "SELECT owner_id, name, COUNT(*) FROM projects "
            "GROUP BY owner_id, name HAVING COUNT(*) > 1",
            "projects(owner_id, name)",
        )
        _ensure_unique_index(insp, "projects", "uq_projects_owner_name", ["owner_id", "name"])

    if "adjudications" in insp.get_table_names() and not _has_named_unique(
        insp, "adjudications", "uq_adjudications_group_attempt",
    ):
        _require_no_duplicates(
            bind,
            "SELECT alert_group_id, attempt, COUNT(*) FROM adjudications "
            "GROUP BY alert_group_id, attempt HAVING COUNT(*) > 1",
            "adjudications(alert_group_id, attempt)",
        )
        _ensure_unique_index(
            insp, "adjudications", "uq_adjudications_group_attempt",
            ["alert_group_id", "attempt"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table, name in (
        ("adjudications", "uq_adjudications_group_attempt"),
        ("projects", "uq_projects_owner_name"),
    ):
        if table not in insp.get_table_names():
            continue
        indexes = {i["name"] for i in insp.get_indexes(table)}
        uniques = {u["name"] for u in insp.get_unique_constraints(table) if u.get("name")}
        if name in indexes:
            op.drop_index(name, table_name=table)
        elif name in uniques:
            op.drop_constraint(name, table_name=table, type_="unique")
