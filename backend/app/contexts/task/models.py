from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.base import BaseModel


class Task(BaseModel):
    """漏洞验证任务"""
    __tablename__ = "tasks"

    project_address: Mapped[str] = mapped_column(String(1024), nullable=False, comment="项目地址 (Git URL)")
    project_ref: Mapped[str | None] = mapped_column(String(255), comment="分支/commit/tag")
    project_ref_type: Mapped[str | None] = mapped_column(
        String(16), comment="branch | tag | commit；空=自动推断",
    )
    clone_depth: Mapped[int | None] = mapped_column(
        Integer, default=1, comment="git clone --depth；0=全量 clone",
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id"), index=True,
        comment="关联 Project(P1 新增,project_address 保留兼容)",
    )
    lab_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("labs.id"), index=True,
        comment="共用靶场 Lab",
    )
    source_type: Mapped[str] = mapped_column(String(20), default="git", comment="git | local_upload")
    vulnerability_description: Mapped[str] = mapped_column(Text, nullable=False, comment="漏洞描述")
    vulnerability_reasoning: Mapped[str | None] = mapped_column(Text, comment="漏洞推理过程")
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True,
        comment="pending | queued | running | needs_review | completed | failed | cancelled | archived"
    )
    verdict: Mapped[str | None] = mapped_column(
        String(30), index=True,
        comment="6 档判定: confirmed|partial|code_reachable|code_smell|false_positive|not_reproduced",
    )
    priority: Mapped[str] = mapped_column(String(10), default="medium", comment="low | medium | high | critical")
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    credential_refs: Mapped[str] = mapped_column(
        Text, default="[]", comment="关联凭据 id 的 JSON 数组（P1-6 Credential Proxy）"
    )

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
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="实际开始执行时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="执行完成时间"
    )
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
    node_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("node_runs.id"), index=True,
        comment="所属 NodeRun(节点化后,事件归属节点)",
    )
    event_type: Mapped[str] = mapped_column(
        String(50), index=True,
        comment="phase.updated | tool.call.* | evidence.created | agent.completed | agent.failed"
    )
    payload: Mapped[str] = mapped_column(Text, default="{}", comment="JSON 事件载荷")
    source: Mapped[str] = mapped_column(String(50), default="claude-code")

    # 关系
    run: Mapped["TaskRun"] = relationship(back_populates="events")

    __table_args__ = (
        Index("idx_agent_events_run_seq", "run_id", "sequence", unique=True),
    )


class NodeRun(BaseModel):
    """节点级执行记录 — 6 节点编排的核心。

    一个 TaskRun 下挂 6 个 NodeRun(node_index 0-5),各自记录
    input/output JSON、状态、排障 attempt。断点续跑时复用已完成的 output_json。
    """
    __tablename__ = "node_runs"

    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_runs.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    node_index: Mapped[int] = mapped_column(comment="0-5")
    node_key: Mapped[str] = mapped_column(
        String(20),
        comment="source|profile|env_ready|audit|reproduce|report",
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending",
        comment="pending|running|completed|failed|skipped|cancelled",
    )
    input_json: Mapped[str] = mapped_column(Text, default="{}", comment="节点输入(前序 output 组装)")
    output_json: Mapped[str] = mapped_column(Text, default="{}", comment="节点结构化产出(交接契约)")
    attempt: Mapped[int] = mapped_column(default=1, comment="排障重试计数(节点 2 用,max 5)")
    agent_session_id: Mapped[str | None] = mapped_column(String(128), comment="AI 节点 SDK session_id")
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_node_runs_run_idx", "run_id", "node_index", unique=True),
        Index("idx_node_runs_task_key", "task_id", "node_key"),
    )

    def __repr__(self) -> str:
        return f"<NodeRun [{self.node_index}:{self.node_key}] {self.status}>"


class NodeRunFailure(BaseModel):
    """节点失败语料索引：指向 crucible-task 上的 node_run 包。"""
    __tablename__ = "node_run_failures"

    owner_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_runs.id"), index=True)
    node_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("node_runs.id"), index=True)
    node_key: Mapped[str] = mapped_column(String(20), nullable=False)
    error_class: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    failed_stage: Mapped[str | None] = mapped_column(String(40))
    language: Mapped[str | None] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(Integer, default=1)
    bundle_key: Mapped[str] = mapped_column(String(512), nullable=False)
    bucket: Mapped[str] = mapped_column(String(64), nullable=False, default="crucible-task")

    __table_args__ = (
        UniqueConstraint("run_id", "node_key", name="uq_node_run_failures_run_node"),
    )
