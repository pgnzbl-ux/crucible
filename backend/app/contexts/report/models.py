from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import BaseModel


class Report(BaseModel):
    """漏洞验证报告 — 由 Agent 分析结果生成"""
    __tablename__ = "reports"

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_runs.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(36), index=True)

    status: Mapped[str] = mapped_column(
        String(20), default="draft",
        comment="draft | generated | published | archived"
    )
    conclusion: Mapped[str] = mapped_column(
        String(20), default="unconfirmed",
        comment="exists | not_exists | unconfirmed"
    )
    title: Mapped[str] = mapped_column(String(255), default="漏洞验证报告")
    summary: Mapped[str | None] = mapped_column(Text, comment="结论摘要")
    reasoning: Mapped[str | None] = mapped_column(Text, comment="完整分析推理")
    evidence_summary: Mapped[str | None] = mapped_column(Text, comment="证据摘要（JSON）")
    artifact_key: Mapped[str | None] = mapped_column(String(512), comment="报告产物在 MinIO 的 key")
    published_at: Mapped[datetime | None] = mapped_column(comment="发布时间")

    # 关系 — 仅同 Context 内
    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="report", order_by="Evidence.created_at.asc()"
    )

    __table_args__ = (
        Index("idx_reports_task", "task_id"),
        Index("idx_reports_owner_status", "owner_id", "status"),
    )

    def __repr__(self) -> str:
        return f"<Report {self.id[:8]} [{self.conclusion}]>"


class Evidence(BaseModel):
    """证据文件 — 指向 MinIO 中的对象"""
    __tablename__ = "evidences"

    report_id: Mapped[str] = mapped_column(String(36), ForeignKey("reports.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, comment="MinIO 对象 key")
    bucket: Mapped[str] = mapped_column(String(64), default="crucible-evidence")
    file_name: Mapped[str] = mapped_column(String(255), comment="原始文件名")
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(default=0)
    kind: Mapped[str] = mapped_column(
        String(20), default="artifact",
        comment="artifact | log | screenshot | poc"
    )

    # 关系
    report: Mapped["Report"] = relationship(back_populates="evidence")

    __table_args__ = (
        Index("idx_evidences_report", "report_id"),
    )

    def __repr__(self) -> str:
        return f"<Evidence {self.file_name} ({self.size_bytes}B)>"
