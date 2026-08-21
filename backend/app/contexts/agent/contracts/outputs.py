"""公开 Handoff（下游可读）与从完整 output_json 投影。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _HandoffBase(BaseModel):
    """从完整 output 投影时忽略私有/调试字段；组装下游 Input 时只带公开键。"""

    model_config = ConfigDict(extra="ignore")


class SourceHandoff(_HandoffBase):
    commit_sha: str | None = None
    repo_dirname: str | None = None
    workspace_path: str | None = None
    project_path: str | None = None
    source_path: str | None = None  # 兼容旧落库 / 单测


class ProfileHandoff(_HandoffBase):
    is_web: bool | None = None
    language: str | None = None
    framework: str | None = None
    port: int | None = None
    has_dockerfile: bool | None = None
    has_compose: bool | None = None
    detected_services: list[Any] | None = None
    start_command: str | None = None
    non_web_reason: str | None = None


class EnvReadyHandoff(_HandoffBase):
    target_url: str | None = None
    compose_path: str | None = None
    transport_shape: dict[str, Any] = Field(default_factory=dict)
    initial_creds: dict[str, Any] = Field(default_factory=dict)
    started_containers: list[Any] = Field(default_factory=list)


class AuditHandoff(_HandoffBase):
    gate_verdict: str | None = None
    gate_reason: str | None = None
    kill_chain: Any = None
    defense_layers: Any = None
    payloads: list[Any] | None = None
    runtime_dependent: bool | None = None
    core_claim: str | None = None
    unresolved_facts: list[Any] | None = None


class AuditForReproduce(_HandoffBase):
    """reproduce 只吃 audit 子集，禁止整包透传。"""

    gate_verdict: str | None = None
    gate_reason: str | None = None
    kill_chain: Any = None
    core_claim: str | None = None
    payloads: list[Any] | None = None
    runtime_dependent: bool | None = None
    unresolved_facts: list[Any] | None = None


class ReproduceHandoff(_HandoffBase):
    verdict: str | None = None
    reproduced: bool | None = None
    attempts: list[Any] | None = None
    evidence: Any = None
    screenshots: Any = None
    cvss: Any = None
    vulnerable_file: str | None = None
    poc: dict[str, Any] | None = None


class ReportHandoff(_HandoffBase):
    report_data: Any = None
    final_verdict: str | None = None
    cvss: Any = None
    vulnerable_file: str | None = None
    poc: Any = None
    authored_by: str | None = None


HANDOFF_BY_KEY: dict[str, type[_HandoffBase]] = {
    "source": SourceHandoff,
    "profile": ProfileHandoff,
    "env_ready": EnvReadyHandoff,
    "audit": AuditHandoff,
    "reproduce": ReproduceHandoff,
    "report": ReportHandoff,
}


def project_handoff(node_key: str, raw: dict[str, Any] | None) -> _HandoffBase:
    """从完整 output_json 投影公开 Handoff（断点续跑兼容多余字段）。"""
    cls = HANDOFF_BY_KEY.get(node_key)
    if cls is None:
        raise KeyError(f"未知节点 handoff: {node_key}")
    return cls.model_validate(raw or {})


def audit_for_reproduce(raw: dict[str, Any] | None) -> AuditForReproduce:
    return AuditForReproduce.model_validate(raw or {})
