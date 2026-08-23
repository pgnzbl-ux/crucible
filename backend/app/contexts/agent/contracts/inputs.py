"""各节点声明的 Input（编排器组装，节点只吃这份）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .outputs import (
    AuditForReproduce,
    AuditHandoff,
    ClusterHandoff,
    DispatchHandoff,
    EngineScanHandoff,
    EnvReadyHandoff,
    ProfileHandoff,
    ReproduceHandoff,
    SourceHandoff,
    TriageHandoff,
)


class _InputBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceInput(_InputBase):
    project_address: str
    project_ref: str | None = None
    project_ref_type: str | None = None
    clone_depth: int | None = 1
    source_type: str = "git"
    host_workdir: str
    source_path: str


class ProfileInput(_InputBase):
    source: SourceHandoff
    host_workdir: str
    source_path: str  # 规则引擎扫盘根；通常等于 source.project_path


class _RepoInput(_InputBase):
    """扫描/聚类类节点的公共仓库锚点。"""

    source: SourceHandoff
    host_workdir: str
    source_path: str


class ScanGitleaksInput(_RepoInput):
    pass


class ScanOsvInput(_RepoInput):
    pass


class ScanSemgrepInput(_RepoInput):
    profile: ProfileHandoff  # 只消费 semgrep_configs；语言选择禁止节点内启发式


class ClusterInput(_RepoInput):
    # 仅作状态摘要(数量/引擎)，真实 findings 由 cluster 节点从 DB 按 task_id 读取
    scans: list[EngineScanHandoff] = []
    profile: ProfileHandoff | None = None  # 决定函数索引语言；缺失则扫全部已支持语言


class TriageInput(_RepoInput):
    cluster: ClusterHandoff


class DispatchInput(_RepoInput):
    triage: TriageHandoff
    profile: ProfileHandoff  # NON_WEB 判定：非 web 不选主线索(§6.4)


class EnvReadyInput(_InputBase):
    source: SourceHandoff
    profile: ProfileHandoff


class AuditInput(_InputBase):
    source: SourceHandoff
    profile: ProfileHandoff
    # 验证任务：人给的线索；审计任务为空串，改吃 dispatch 主线索
    vulnerability_description: str = ""
    dispatch: DispatchHandoff | None = None


class ReproduceInput(_InputBase):
    source: SourceHandoff
    env_ready: EnvReadyHandoff
    audit: AuditForReproduce
    vulnerability_description: str = ""


class ReportInput(_InputBase):
    profile: ProfileHandoff
    env_ready: EnvReadyHandoff
    audit: AuditHandoff
    reproduce: ReproduceHandoff
    vulnerability_description: str = ""
    project_address: str
    expected_verdict: str | None = None
    document_kind: str | None = None
