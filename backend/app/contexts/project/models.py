"""项目源码管理 — 一次注册、多任务复用。

Project 记录项目元数据 + 节点 1 画像缓存(language/framework/is_web),
后续任务复用画像省 AI。P1 阶段不长期持有源码(每任务按 run clone)。
"""
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import BaseModel


class Project(BaseModel):
    """项目(Git 仓库)的元数据与画像。"""
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    git_url: Mapped[str] = mapped_column(String(1024), nullable=False, index=True, comment="Git URL")
    default_ref: Mapped[str | None] = mapped_column(String(255), comment="默认分支/tag")
    description: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    # 节点 1 画像后回填(后续任务复用省 AI)
    detected_language: Mapped[str | None] = mapped_column(String(50))
    detected_framework: Mapped[str | None] = mapped_column(String(100))
    is_web: Mapped[bool | None] = mapped_column(Boolean)
    last_cloned_at: Mapped[datetime | None] = mapped_column(comment="P2 源码缓存复用用")

    __table_args__ = (
        Index("idx_projects_owner", "owner_id"),
    )

    def __repr__(self) -> str:
        return f"<Project {self.name} {self.git_url[:40]}>"
