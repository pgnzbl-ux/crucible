from datetime import datetime

from sqlalchemy import Index, String, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import BaseModel


class Task(BaseModel):
    """漏洞验证任务"""
    __tablename__ = "tasks"

    project_address: Mapped[str] = mapped_column(String(1024), nullable=False, comment="项目地址 (Git URL)")
    project_ref: Mapped[str | None] = mapped_column(String(255), comment="分支/commit/tag")
    source_type: Mapped[str] = mapped_column(String(20), default="git", comment="git | local_upload")
    vulnerability_description: Mapped[str] = mapped_column(Text, nullable=False, comment="漏洞描述")
    vulnerability_reasoning: Mapped[str | None] = mapped_column(Text, comment="漏洞推理过程")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True,
        comment="pending | queued | running | needs_review | completed | failed | cancelled"
    )
    priority: Mapped[str] = mapped_column(String(10), default="medium", comment="low | medium | high | critical")
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)

    # 关系 — 仅 TaskRun（同 Context 内），不用跨 Context ORM 关系
    runs: Mapped[list["TaskRun"]] = relationship(back_populates="task", order_by="TaskRun.created_at.desc()")

    __table_args__ = (
        Index("idx_tasks_owner_status", "owner_id", "status"),
        Index("idx_tasks_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Task {self.id[:8]} [{self.status}]>"


class TaskRun(BaseModel):
    """任务的一次执行运行"""
    __tablename__ = "task_runs"

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="pending",
        comment="pending | preflight | running | completed | failed | cancelled"
    )
    agent_session_id: Mapped[str | None] = mapped_column(String(128), comment="Agent 会话 ID")
    started_at: Mapped[datetime | None] = mapped_column(comment="实际开始执行时间")
    finished_at: Mapped[datetime | None] = mapped_column(comment="执行完成时间")
    error_message: Mapped[str | None] = mapped_column(Text)

    # 关系
    task: Mapped["Task"] = relationship(back_populates="runs")
    events: Mapped[list["AgentEvent"]] = relationship(back_populates="run", order_by="AgentEvent.sequence")

    __table_args__ = (
        Index("idx_task_runs_task_status", "task_id", "status"),
    )


class AgentEvent(BaseModel):
    """Agent 执行流的结构化事件"""
    __tablename__ = "agent_events"

    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_runs.id"), index=True)
    sequence: Mapped[int] = mapped_column(default=0, comment="事件序列号，全局递增")
    task_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(
        String(50), index=True,
        comment="phase.updated | tool.call.* | evidence.created | agent.completed | agent.failed"
    )
    payload: Mapped[str] = mapped_column(Text, default="{}", comment="JSON 事件载荷")
    source: Mapped[str] = mapped_column(String(50), default="claude-code")

    # 关系
    run: Mapped["TaskRun"] = relationship(back_populates="events")

    __table_args__ = (
        Index("idx_agent_events_run_seq", "run_id", "sequence"),
    )
