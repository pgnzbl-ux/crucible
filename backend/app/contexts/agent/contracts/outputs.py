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


class LanguageFact(BaseModel):
    """语言证据事实(discovery-spec §6.0)：文件证据 > 规则引擎 > AI 补全。"""

    model_config = ConfigDict(extra="ignore")

    id: str
    evidence_files: list[str] = Field(default_factory=list)  # 相对仓库根
    source: str = "rules"  # rules | ai；ai 项不进 semgrep_configs
    confidence: float = 1.0


class ProfileHandoff(_HandoffBase):
    is_web: bool | None = None
    languages: list[LanguageFact] = Field(default_factory=list)
    primary_language: str | None = None  # 由 languages 按证据权重派生
    language: str | None = None  # = primary_language，兼容 env_ready 旧 Input
    frameworks: list[str] = Field(default_factory=list)
    framework: str | None = None  # = frameworks[0]，兼容旧字段
    package_managers: list[str] = Field(default_factory=list)
    semgrep_configs: list[str] = Field(default_factory=list)  # 纯函数派生，禁止 AI 写
    osv_manifests: list[str] = Field(default_factory=list)  # scan_osv 加速用
    port: int | None = None
    has_dockerfile: bool | None = None
    has_compose: bool | None = None
    detected_services: list[Any] | None = None
    start_command: str | None = None
    non_web_reason: str | None = None
    profile_source: str | None = None  # rules | rules+ai | cache


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


class EngineScanHandoff(_HandoffBase):
    """扫描节点统一产出(discovery-spec §6.1)：一引擎一节点一 ScanRun。"""

    engine: str = ""
    scan_run_id: str | None = None
    status: str | None = None  # completed | failed | skipped
    finding_count: int = 0


class ClusterHandoff(_HandoffBase):
    group_count: int = 0
    groups_by_cwe: dict[str, int] = Field(default_factory=dict)
    groups_by_engine: dict[str, int] = Field(default_factory=dict)
    groups_by_grade: dict[str, int] = Field(default_factory=dict)
    index_built: bool = False
    index_languages: list[str] = Field(default_factory=list)
    index_symbol_count: int = 0
    finding_count: int = 0
    findings_without_function: int = 0
    bypass_count: int = 0
    downgraded_count: int = 0


class TriageHandoff(_HandoffBase):
    adjudicated_count: int = 0
    skipped_llm_count: int = 0
    skipped_unaudited_count: int = 0  # 兜底残留 clustered（正常应为 0）
    tp_count: int = 0
    fp_count: int = 0
    need_more_count: int = 0


class DispatchHandoff(_HandoffBase):
    """dispatch 产出：合格线索入队计数(discovery-spec §6.4 / v1.2)。"""

    has_lead: bool = False
    queued_count: int = 0
    queued_group_ids: list[str] = []
    # 兼容旧字段：代表组（排序第一）与其描述摘要
    lead_group_id: str | None = None
    lead_description: str | None = None
    review_count: int = 0
    archived_count: int = 0
    skipped_unaudited_count: int = 0


HANDOFF_BY_KEY: dict[str, type[_HandoffBase]] = {
    "source": SourceHandoff,
    "profile": ProfileHandoff,
    "env_ready": EnvReadyHandoff,
    "audit": AuditHandoff,
    "reproduce": ReproduceHandoff,
    "report": ReportHandoff,
    "scan_gitleaks": EngineScanHandoff,
    "scan_osv": EngineScanHandoff,
    "scan_semgrep": EngineScanHandoff,
    "cluster": ClusterHandoff,
    "triage": TriageHandoff,
    "dispatch": DispatchHandoff,
}


def project_handoff(node_key: str, raw: dict[str, Any] | None) -> _HandoffBase:
    """从完整 output_json 投影公开 Handoff（断点续跑兼容多余字段）。"""
    cls = HANDOFF_BY_KEY.get(node_key)
    if cls is None:
        raise KeyError(f"未知节点 handoff: {node_key}")
    return cls.model_validate(raw or {})


def audit_for_reproduce(raw: dict[str, Any] | None) -> AuditForReproduce:
    return AuditForReproduce.model_validate(raw or {})
