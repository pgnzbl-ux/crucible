from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import BaseModel


class Report(BaseModel):
    """代码审计或定向验证报告 — 由 Agent 分析结果生成。"""
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
    title: Mapped[str] = mapped_column(String(255), default="安全分析报告")
    summary: Mapped[str | None] = mapped_column(Text, comment="结论摘要")
    reasoning: Mapped[str | None] = mapped_column(Text, comment="完整分析推理")
    evidence_summary: Mapped[str | None] = mapped_column(Text, comment="证据摘要(JSON,deprecated)")
    artifact_key: Mapped[str | None] = mapped_column(String(512), comment="报告产物在 MinIO 的 key(deprecated)")

    # 结构化字段(阶段 1 新增)
    verdict: Mapped[str | None] = mapped_column(
        String(30), index=True,
        comment="6 档: confirmed|partial|code_reachable|code_smell|false_positive|not_reproduced",
    )
    cvss_score: Mapped[float | None] = mapped_column(Float, index=True)
    severity: Mapped[str | None] = mapped_column(String(20), comment="Critical/High/Medium/Low/None")
    vulnerable_file: Mapped[str | None] = mapped_column(String(1024), comment="漏洞文件定位")
    product_name: Mapped[str | None] = mapped_column(String(255), comment="产品名称（本地上传任务=项目名）")
    affected_version: Mapped[str | None] = mapped_column(String(64), comment="影响版本 ref@commit（本次复现版本）")
    project_address: Mapped[str | None] = mapped_column(String(512), comment="项目地址（git 地址；本地上传=项目名）")
    report_data: Mapped[str | None] = mapped_column(Text, comment="8 节结构化 JSON(对齐 report_template)")
    md_artifact_key: Mapped[str | None] = mapped_column(String(512), comment="MinIO:原始 md key")
    docx_artifact_key: Mapped[str | None] = mapped_column(String(512), comment="MinIO:导出 docx key")
    poc_language: Mapped[str | None] = mapped_column(String(16), comment="python/bash/other")
    poc_filename: Mapped[str | None] = mapped_column(String(255))
    poc_code: Mapped[str | None] = mapped_column(Text, comment="完整 PoC 源码")
    poc_usage: Mapped[str | None] = mapped_column(String(1024))

    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="发布时间"
    )

    # 关系 — 仅同 Context 内
    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="report", order_by="Evidence.created_at.asc()"
    )

    __table_args__ = (
        Index("idx_reports_task", "task_id"),
        Index("idx_reports_owner_status", "owner_id", "status"),
        Index("uq_reports_run_id", "run_id", unique=True),
    )

    def __repr__(self) -> str:
        return f"<Report {self.id[:8]} [{self.conclusion}]>"


class Evidence(BaseModel):
    """证据文件 — 指向 MinIO 中的对象"""
    __tablename__ = "evidences"

    report_id: Mapped[str] = mapped_column(String(36), ForeignKey("reports.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, comment="MinIO 对象 key")
    bucket: Mapped[str] = mapped_column(String(64), default="crucible-task")
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
