"""节点交接契约 — 公开 Input / Handoff / ControlSignals。

见 docs/agent-node-contracts.md。AI submit_result schema 仍在
infrastructure/agent-runner/runner/node_schemas.py，不在本包重复。
"""
from __future__ import annotations

from .handoff_store import HandoffStore, InputAssembler, TaskScalars
from .inputs import (
    AuditInput,
    EnvReadyInput,
    ProfileInput,
    ReportInput,
    ReproduceInput,
    SourceInput,
)
from .outputs import (
    AuditForReproduce,
    AuditHandoff,
    EnvReadyHandoff,
    ProfileHandoff,
    ReportHandoff,
    ReproduceHandoff,
    SourceHandoff,
    audit_for_reproduce,
    project_handoff,
)
from .registry import DEFAULT_PIPELINE, NODE_BY_KEY, NodeSpec, SkipWhen, node_by_key
from .signals import ControlSignals

__all__ = [
    "AuditForReproduce",
    "AuditHandoff",
    "AuditInput",
    "ControlSignals",
    "DEFAULT_PIPELINE",
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
    "SkipWhen",
    "SourceHandoff",
    "SourceInput",
    "TaskScalars",
    "audit_for_reproduce",
    "node_by_key",
    "project_handoff",
]
