from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import BaseModel


class Lab(BaseModel):
    """按用户、项目和 commit 复用的靶场。"""

    __tablename__ = "labs"

    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    commit_sha: Mapped[str] = mapped_column(
        String(64), comment="git SHA-1 或上传包 sha256"
    )
    status: Mapped[str] = mapped_column(String(20), default="creating", index=True)
    compose_project: Mapped[str] = mapped_column(String(255))
    workdir: Mapped[str] = mapped_column(String(1024))
    target_url: Mapped[str | None] = mapped_column(String(1024))
    compose_path: Mapped[str | None] = mapped_column(String(1024))
    transport_shape: Mapped[str] = mapped_column(Text, default="{}")
    initial_creds: Mapped[str] = mapped_column(Text, default="{}")
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    creator_task_id: Mapped[str | None] = mapped_column(String(36))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "project_id",
            "commit_sha",
            name="uq_labs_owner_project_sha",
        ),
    )
