"""节点交接契约 — 公开 Input / Handoff / ControlSignals。

见 docs/discovery-spec.md §4.2/§6.1/§6.2。AI submit_result
schema 仍在 infrastructure/agent-runner/runner/node_schemas.py，不在本包重复。
"""
from __future__ import annotations

from .handoff_store import HandoffStore, InputAssembler, TaskScalars
from .inputs import (
    AuditInput,
    ClusterInput,
    DispatchInput,
    EnvReadyInput,
    ProfileInput,
    ReportInput,
    ReproduceInput,
    ScanGitleaksInput,
    ScanOsvInput,
    ScanSemgrepInput,
    ScreenInput,
    SourceInput,
    TriageInput,
)
from .outputs import (
    AuditForReproduce,
    AuditHandoff,
    ClusterHandoff,
    DispatchHandoff,
    EngineScanHandoff,
    EnvReadyHandoff,
    ProfileHandoff,
    ReportHandoff,
    ReproduceHandoff,
    ScreenHandoff,
    SourceHandoff,
    TriageHandoff,
    audit_for_reproduce,
    project_handoff,
)
from .registry import (
    DEFAULT_PIPELINE,
    NODE_BY_KEY,
    NodeSpec,
    SkipWhen,
    node_by_key,
    validate_pipeline,
)
from .signals import ControlSignals

__all__ = [
    "AuditForReproduce",
    "AuditHandoff",
    "AuditInput",
    "ClusterHandoff",
    "ClusterInput",
    "ControlSignals",
    "DEFAULT_PIPELINE",
    "DispatchHandoff",
    "DispatchInput",
    "EngineScanHandoff",
    "EnvReadyHandoff",
    "EnvReadyInput",
    "HandoffStore",
    "InputAssembler",
    "NODE_BY_KEY",
    "NodeSpec",
    "ProfileHandoff",
    "ProfileInput",
    "ReportHandoff",
    "ReportInput",
    "ReproduceHandoff",
    "ReproduceInput",
    "ScanGitleaksInput",
    "ScanOsvInput",
    "ScanSemgrepInput",
    "ScreenHandoff",
    "ScreenInput",
    "SkipWhen",
    "SourceHandoff",
    "SourceInput",
    "TaskScalars",
    "TriageHandoff",
    "TriageInput",
    "audit_for_reproduce",
    "node_by_key",
    "project_handoff",
    "validate_pipeline",
]
