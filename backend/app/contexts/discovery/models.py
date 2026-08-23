"""discovery context 数据模型。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import BaseModel


class ScanRun(BaseModel):
    """一次引擎扫描运行；与扫描节点 NodeRun 一一对应。"""

    __tablename__ = "scan_runs"

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_runs.id"), index=True)
    node_run_id: Mapped[str] = mapped_column(String(36), index=True)  # 逻辑指向 node_runs.id
    engine: Mapped[str] = mapped_column(String(20), comment="semgrep | gitleaks | osv")
    status: Mapped[str] = mapped_column(
        String(20), default="running",
        comment="running | completed | failed | skipped",
    )
    config_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="规则包/参数快照，可复现")
    finding_count: Mapped[int] = mapped_column(Integer, default=0)
    sarif_key: Mapped[str | None] = mapped_column(String(500), comment="MinIO crucible-task 原始 SARIF 归档 key")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<ScanRun {self.engine} [{self.status}]>"
