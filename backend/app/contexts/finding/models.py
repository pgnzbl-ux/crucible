"""finding context 数据模型。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.base import BaseModel


class RawFinding(BaseModel):
    """引擎原始告警(SARIF+ 归一化)。Gitleaks 命中原文入库；LLM/日志路径另脱敏(discovery-spec §8.2)。"""

    __tablename__ = "raw_findings"
    __table_args__ = (
        UniqueConstraint("task_id", "fingerprint", name="uq_raw_findings_task_fp"),
        Index("idx_raw_findings_task_engine", "task_id", "engine"),
    )

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    scan_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("scan_runs.id"), index=True)
    alert_group_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(
            "alert_groups.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_raw_findings_alert_group_id",
        ),
        nullable=True,
        index=True,
    )
    engine: Mapped[str] = mapped_column(String(20), comment="semgrep | gitleaks | osv | api_hunt")
    rule_id: Mapped[str] = mapped_column(String(255), comment='如 "python.sql-injection" / GHSA id / gitleaks 规则名')
    cwe: Mapped[str | None] = mapped_column(String(20), comment="CWE-89；osv 可为空")
    severity: Mapped[str | None] = mapped_column(String(20), comment="引擎原始严重度")
    file_path: Mapped[str] = mapped_column(String(1024))
    line_start: Mapped[int | None] = mapped_column(Integer)
    line_end: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, comment="引擎消息；gitleaks 保留命中原文")
    source_to_sink: Mapped[list[Any] | None] = mapped_column(
        JSON, comment="SARIF codeFlows/threadFlows；无 traces 则 null",
    )
    code_snippet: Mapped[str | None] = mapped_column(Text, comment="命中片段或 OSV 可读摘要")
    fingerprint: Mapped[str] = mapped_column(String(64), comment="sha256(engine+rule_id+file_path+line_start+cwe)")
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="原始条目元数据（展示/降噪用）")

    def __repr__(self) -> str:
        return f"<RawFinding {self.engine}:{self.rule_id} {self.file_path}:{self.line_start}>"


class AlertGroup(BaseModel):
    """聚类后的告警组。状态机见 finding service(discovery-spec §5.3)。"""

    __tablename__ = "alert_groups"
    __table_args__ = (
        UniqueConstraint("task_id", "group_key", name="uq_alert_groups_task_key"),
        Index("idx_alert_groups_task_status", "task_id", "status"),
        Index("idx_alert_groups_updated_at", "updated_at"),
    )

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    group_key: Mapped[str] = mapped_column(String(64))
    cwe: Mapped[str | None] = mapped_column(String(20))
    file_path: Mapped[str] = mapped_column(String(1024))
    function_symbol: Mapped[str | None] = mapped_column(String(255), comment="tree-sitter 解析出的函数名")
    line_span: Mapped[str | None] = mapped_column(String(32), comment='"120-145"')
    member_count: Mapped[int] = mapped_column(Integer, default=1)
    representative_finding_id: Mapped[str] = mapped_column(String(36), ForeignKey("raw_findings.id"))
    engine_set: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(
        String(20), default="new",
        comment="new | clustered | adjudicated | needs_review | dispatched | resolved",
    )
    clue_grade: Mapped[str | None] = mapped_column(String(2), comment="A | B | F；C 不进本组")
    ai_verdict: Mapped[str | None] = mapped_column(
        String(20), comment="tp | fp | need_more_context | bypass；未审保持空",
    )
    ai_confidence: Mapped[float | None] = mapped_column(Float, comment="0–1")
    # 判决来源溯源（级联管线）：null = 全价 agent(历史行)；agent | fast_model |
    # rule | carryover | propagated。复核台据此区分"亲审"与"传播/前置"。
    verdict_source: Mapped[str | None] = mapped_column(String(20))
    # T3 同根因族键（rule|cwe|module 的哈希）；代表审议后族内传播判决
    family_key: Mapped[str | None] = mapped_column(String(64), index=True)
    priority: Mapped[str | None] = mapped_column(String(10), comment="high | medium | low")
    resolution: Mapped[str | None] = mapped_column(String(20), comment="confirmed | false_positive | ignored(终态细分)")
    verification_basis: Mapped[str | None] = mapped_column(
        String(20), comment="lab | code_path；与终认 LeadRun 对齐",
    )
    vuln_report: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, comment="终局成功时一漏洞一份报告 JSON（discovery-spec §11.1）",
    )

    def __repr__(self) -> str:
        return f"<AlertGroup {self.cwe} {self.file_path} [{self.status}]>"


class Adjudication(BaseModel):
    """AI 二审判决全量归档(prompt+response 可回放)。"""

    __tablename__ = "adjudications"
    __table_args__ = (
        UniqueConstraint("alert_group_id", "attempt", name="uq_adjudications_group_attempt"),
    )

    alert_group_id: Mapped[str] = mapped_column(String(36), ForeignKey("alert_groups.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, comment="need_more_context 重问 +1")
    provider_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("llm_providers.id"), index=True)
    model: Mapped[str | None] = mapped_column(String(120))
    verdict: Mapped[str] = mapped_column(String(20), comment="tp | fp | need_more_context | parse_failed")
    confidence: Mapped[float | None] = mapped_column(Float)
    why: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict]] = mapped_column(JSON, default=list, comment="[{file, lines}]；判决必须带证据")
    need: Mapped[list[str]] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text, comment="1～3 句漏洞简述（§2.3.1）")
    reasoning: Mapped[str | None] = mapped_column(Text, comment="代码/依赖推理过程（§2.3.1）")
    context_log: Mapped[list[dict]] = mapped_column(JSON, default=list, comment="每轮上下文组装记录")
    prompt_text: Mapped[str] = mapped_column(Text)
    response_text: Mapped[str] = mapped_column(Text)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, comment="{prompt_tokens, completion_tokens}")


class ReviewAction(BaseModel):
    """人工复核动作 — 飞轮数据，第一天就存(KNW-01)。"""

    __tablename__ = "review_actions"
    __table_args__ = (
        Index("idx_review_actions_group", "alert_group_id"),
    )

    alert_group_id: Mapped[str] = mapped_column(String(36), ForeignKey("alert_groups.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(
        String(20),
        comment="confirm | reject | revise_cwe | adjust_confidence | revive | dispatch",
    )
    reason_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason_text: Mapped[str | None] = mapped_column(Text)


class LeadRun(BaseModel):
    """discovery 终认队列中的单条线索执行记录(§4.4 / §6.4)。"""

    __tablename__ = "lead_runs"
    __table_args__ = (
        UniqueConstraint("run_id", "alert_group_id", name="uq_lead_runs_run_group"),
        Index("idx_lead_runs_task_status", "task_id", "status"),
    )

    task_id: Mapped[str] = mapped_column(String(36), ForeignKey("tasks.id"), index=True)
    run_id: Mapped[str] = mapped_column(String(36), ForeignKey("task_runs.id"), index=True)
    alert_group_id: Mapped[str] = mapped_column(String(36), ForeignKey("alert_groups.id"), index=True)
    queue_position: Mapped[int] = mapped_column(Integer, default=0)
    lead_description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20), default="queued",
        comment="queued | running | completed | failed | skipped",
    )
    verdict: Mapped[str | None] = mapped_column(
        String(30), comment="六档：confirmed|partial|...；失败可空",
    )
    verification_basis: Mapped[str | None] = mapped_column(
        String(20), comment="lab | code_path",
    )
    gate_verdict: Mapped[str | None] = mapped_column(String(20))
    audit_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    reproduce_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<LeadRun {self.alert_group_id[:8]} [{self.status}]>"
