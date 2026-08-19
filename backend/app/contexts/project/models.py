"""项目源码管理 — 一次注册、多任务复用。

Project 记录项目元数据 + 画像缓存。
SourceArtifact 记录每次落到 MinIO 的源码包（访问地址、规范化 Git URL、space/project、ref）。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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

    detected_language: Mapped[str | None] = mapped_column(String(50))
    detected_framework: Mapped[str | None] = mapped_column(String(100))
    is_web: Mapped[bool | None] = mapped_column(Boolean)
    last_cloned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一次源码落地（clone 或缓存命中）"
    )

    __table_args__ = (
        Index("idx_projects_owner", "owner_id"),
    )

    def __repr__(self) -> str:
        return f"<Project {self.name} {self.git_url[:40]}>"


class SourceArtifact(BaseModel):
    """一次 clone/缓存对应的 MinIO 源码包。"""
    __tablename__ = "source_artifacts"

    owner_id: Mapped[str] = mapped_column(
        String(36), nullable=False, index=True, comment="任务所有者，缓存按用户隔离"
    )
    git_url: Mapped[str] = mapped_column(
        String(1024), nullable=False, comment="规范化地址（已去 .git 与 https userinfo）"
    )
    git_host: Mapped[str] = mapped_column(String(255), nullable=False, comment="github.com 等")
    project_key: Mapped[str] = mapped_column(
        String(512), nullable=False, index=True, comment="space/project，如 siteboon/claudecodeui"
    )
    repo_dirname: Mapped[str] = mapped_column(String(255), nullable=False, comment="落地目录名")
    ref_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="branch|tag|commit")
    ref_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="main / v1.0.0 / sha")
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    profile_json: Mapped[str | None] = mapped_column(
        Text, comment="该 commit 的画像 JSON；commit_sha 变更时清空"
    )
    bucket: Mapped[str] = mapped_column(String(64), nullable=False, default="crucible-durable")
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    object_url: Mapped[str] = mapped_column(String(1024), nullable=False, comment="MinIO 访问地址")
    size_bytes: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "git_host",
            "project_key",
            "ref_type",
            "ref_name",
            name="uq_source_artifacts_owner_host_ref",
        ),
    )
