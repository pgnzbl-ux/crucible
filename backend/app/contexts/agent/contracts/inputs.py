"""各节点声明的 Input（编排器组装，节点只吃这份）。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from .outputs import (
    AuditForReproduce,
    AuditHandoff,
    EnvReadyHandoff,
    ProfileHandoff,
    ReproduceHandoff,
    SourceHandoff,
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


class EnvReadyInput(_InputBase):
    source: SourceHandoff
    profile: ProfileHandoff


class AuditInput(_InputBase):
    source: SourceHandoff
    profile: ProfileHandoff
    vulnerability_description: str


class ReproduceInput(_InputBase):
    source: SourceHandoff
    env_ready: EnvReadyHandoff
    audit: AuditForReproduce
    vulnerability_description: str


class ReportInput(_InputBase):
    profile: ProfileHandoff
    env_ready: EnvReadyHandoff
    audit: AuditHandoff
    reproduce: ReproduceHandoff
    vulnerability_description: str
    project_address: str
    expected_verdict: str | None = None
    document_kind: str | None = None


INPUT_BY_KEY: dict[str, type[BaseModel]] = {
    "source": SourceInput,
    "profile": ProfileInput,
    "env_ready": EnvReadyInput,
    "audit": AuditInput,
    "reproduce": ReproduceInput,
    "report": ReportInput,
}
