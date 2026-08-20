"""同步 ORM 与库内列注释（upload / Anthropic 兼容语义）

Revision ID: h1c4d8e05f26
Revises: g7b3e9a02c15
Create Date: 2026-08-20
"""
from typing import Sequence, Union

from alembic import op

revision: str = "h1c4d8e05f26"
down_revision: Union[str, None] = "g7b3e9a02c15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column, new_comment, old_comment_for_downgrade)
_COMMENTS: list[tuple[str, str, str, str | None]] = [
    ("labs", "commit_sha", "git SHA-1 或上传包 sha256", None),
    (
        "llm_providers",
        "provider_type",
        "deepseek | anthropic | custom（均为 Anthropic Messages 兼容端点）",
        "deepseek | openai_compat | anthropic | custom",
    ),
    (
        "projects",
        "git_url",
        "Git URL，或 upload://local/{slug}",
        "Git URL",
    ),
    ("projects", "source_type", "git | local_upload", None),
    (
        "source_artifacts",
        "ref_type",
        "branch|tag|commit|upload",
        "branch|tag|commit",
    ),
    (
        "source_artifacts",
        "ref_name",
        "main / v1.0.0 / sha / local",
        "main / v1.0.0 / sha",
    ),
    ("source_artifacts", "commit_sha", "git SHA-1 或上传包 sha256", None),
    (
        "tasks",
        "project_ref_type",
        "branch | tag | commit；空=自动推断",
        None,
    ),
    ("tasks", "clone_depth", "git clone --depth；0=全量 clone", None),
]


def _escape(comment: str) -> str:
    return comment.replace("'", "''")


def upgrade() -> None:
    for table, column, new_comment, _old in _COMMENTS:
        op.execute(
            f"COMMENT ON COLUMN {table}.{column} IS '{_escape(new_comment)}'"
        )


def downgrade() -> None:
    for table, column, _new, old in _COMMENTS:
        if old is None:
            op.execute(f"COMMENT ON COLUMN {table}.{column} IS NULL")
        else:
            op.execute(
                f"COMMENT ON COLUMN {table}.{column} IS '{_escape(old)}'"
            )
