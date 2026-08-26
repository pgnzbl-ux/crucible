"""HandoffStore + InputAssembler — 编排器装配 typed Input，节点不抠 previous。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .inputs import (
    ApiHuntInput,
    ApiInventoryInput,
    AuditInput,
    ClusterInput,
    DispatchInput,
    EnvReadyInput,
    FinalizeInput,
    LeadVerifyInput,
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
    ApiHuntHandoff,
    ApiInventoryHandoff,
    AuditHandoff,
    DispatchHandoff,
    EngineScanHandoff,
    EnvReadyHandoff,
    FinalizeHandoff,
    LeadVerifyHandoff,
    ProfileHandoff,
    ReproduceHandoff,
    SourceHandoff,
    audit_for_reproduce,
    project_handoff,
)
from .signals import ControlSignals


@dataclass(frozen=True)
class TaskScalars:
    """从 Task 抽出的标量，供 Assembler 使用（避免节点 import Task ORM）。"""

    project_address: str
    project_ref: str | None
    project_ref_type: str | None
    clone_depth: int | None
    source_type: str
    vulnerability_description: str
    host_workdir: str
    source_path: str
    task_type: str = "verify"  # verify | discovery(discovery-spec §4.2.3)


class HandoffStore:
    """按 node_key 存完整 output；对外投影 Handoff / ControlSignals。"""

    def __init__(self) -> None:
        self._raw: dict[str, dict[str, Any]] = {}

    def set(self, node_key: str, output: dict[str, Any] | None) -> None:
        self._raw[node_key] = dict(output or {})

    def pop(self, node_key: str) -> dict[str, Any] | None:
        return self._raw.pop(node_key, None)

    def get_raw(self, node_key: str) -> dict[str, Any]:
        return dict(self._raw.get(node_key) or {})

    def as_previous_outputs(self) -> dict[str, dict[str, Any]]:
        """兼容失败打包 / env_ready 内部桥接；不是节点公开 API。"""
        return {k: dict(v) for k, v in self._raw.items()}

    def project(self, node_key: str):
        return project_handoff(node_key, self._raw.get(node_key))

    def signals(self, verify_mode: bool = False) -> ControlSignals:
        profile = self.get_raw("profile")
        audit = self.get_raw("audit")
        dispatch = self.get_raw("dispatch")
        is_web = profile.get("is_web") if isinstance(profile.get("is_web"), bool) else None
        gate = audit.get("gate_verdict")
        gate_verdict = str(gate) if gate is not None else None
        lead = dispatch.get("has_lead") if dispatch else None
        queued = dispatch.get("queued_count") if dispatch else None
        if isinstance(lead, bool):
            has_dispatch_lead = lead
        elif isinstance(queued, int):
            has_dispatch_lead = queued > 0
        else:
            has_dispatch_lead = None
        return ControlSignals(
            is_web=is_web,
            gate_verdict=gate_verdict,
            has_dispatch_lead=has_dispatch_lead,
            verify_mode=verify_mode,
        )


class InputAssembler:
    """按节点声明组装 Input；序列化后可写入 NodeRun.input_json / .node.json。"""

    @staticmethod
    def assemble(node_key: str, store: HandoffStore, task: TaskScalars) -> Any:
        if node_key == "source":
            return SourceInput(
                project_address=task.project_address,
                project_ref=task.project_ref,
                project_ref_type=task.project_ref_type,
                clone_depth=task.clone_depth,
                source_type=task.source_type or "git",
                host_workdir=task.host_workdir,
                source_path=task.source_path,
            )
        if node_key == "profile":
            source = SourceHandoff.model_validate(store.get_raw("source"))
            root = source.project_path or task.source_path
            return ProfileInput(source=source, host_workdir=task.host_workdir, source_path=root)
        if node_key == "env_ready":
            return EnvReadyInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                profile=ProfileHandoff.model_validate(store.get_raw("profile")),
            )
        if node_key == "scan_gitleaks":
            return ScanGitleaksInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
            )
        if node_key == "scan_osv":
            profile_raw = store.get_raw("profile")
            return ScanOsvInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
                profile=ProfileHandoff.model_validate(profile_raw) if profile_raw else None,
            )
        if node_key == "scan_semgrep":
            return ScanSemgrepInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                profile=ProfileHandoff.model_validate(store.get_raw("profile")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
            )
        if node_key == "api_inventory":
            return ApiInventoryInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                profile=ProfileHandoff.model_validate(store.get_raw("profile")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
            )
        if node_key == "cluster":
            scans = [
                EngineScanHandoff.model_validate(store.get_raw(key))
                for key in ("scan_gitleaks", "scan_osv", "scan_semgrep")
            ]
            hunt_raw = store.get_raw("api_hunt")
            if hunt_raw:
                scans.append(EngineScanHandoff.model_validate(hunt_raw))
            profile_raw = store.get_raw("profile")
            return ClusterInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
                scans=scans,
                profile=ProfileHandoff.model_validate(profile_raw) if profile_raw else None,
            )
        if node_key == "api_hunt":
            profile_raw = store.get_raw("profile")
            return ApiHuntInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
                inventory=ApiInventoryHandoff.model_validate(store.get_raw("api_inventory")),
                profile=ProfileHandoff.model_validate(profile_raw) if profile_raw else None,
            )
        if node_key == "screen":
            cluster_raw = store.get_raw("cluster")
            return ScreenInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
                cluster=(
                    project_handoff("cluster", cluster_raw) if cluster_raw else None
                ),
            )
        if node_key == "triage":
            cluster_raw = store.get_raw("cluster")
            return TriageInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
                screen=project_handoff("screen", store.get_raw("screen")),
                cluster=(
                    project_handoff("cluster", cluster_raw) if cluster_raw else None
                ),
            )
        if node_key == "dispatch":
            hunt_raw = store.get_raw("api_hunt")
            return DispatchInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                host_workdir=task.host_workdir,
                source_path=task.source_path,
                triage=project_handoff("triage", store.get_raw("triage")),
                profile=ProfileHandoff.model_validate(store.get_raw("profile")),
                api_hunt=(
                    ApiHuntHandoff.model_validate(hunt_raw) if hunt_raw else None
                ),
            )
        if node_key == "audit":
            # 验证：人给的描述；审计：description 为空串 + dispatch 主线索(discovery-spec §4.2.4)
            dispatch_raw = store.get_raw("dispatch")
            dispatch = (
                DispatchHandoff.model_validate(dispatch_raw)
                if task.task_type != "verify" and dispatch_raw
                else None
            )
            return AuditInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                profile=ProfileHandoff.model_validate(store.get_raw("profile")),
                vulnerability_description=task.vulnerability_description or "" or "",
                dispatch=dispatch,
            )
        if node_key == "reproduce":
            return ReproduceInput(
                source=SourceHandoff.model_validate(store.get_raw("source")),
                env_ready=EnvReadyHandoff.model_validate(store.get_raw("env_ready")),
                audit=audit_for_reproduce(store.get_raw("audit")),
                vulnerability_description=task.vulnerability_description or "",
            )
        if node_key == "lead_verify":
            return LeadVerifyInput(
                dispatch=DispatchHandoff.model_validate(store.get_raw("dispatch")),
                env_ready=EnvReadyHandoff.model_validate(store.get_raw("env_ready")),
            )
        if node_key == "finalize":
            if task.task_type == "discovery":
                return FinalizeInput(
                    profile=ProfileHandoff.model_validate(store.get_raw("profile")),
                    env_ready=EnvReadyHandoff.model_validate(store.get_raw("env_ready")),
                    lead_verify=LeadVerifyHandoff.model_validate(
                        store.get_raw("lead_verify")
                    ),
                )
            return FinalizeInput(
                profile=ProfileHandoff.model_validate(store.get_raw("profile")),
                env_ready=EnvReadyHandoff.model_validate(store.get_raw("env_ready")),
                audit=AuditHandoff.model_validate(store.get_raw("audit")),
                reproduce=ReproduceHandoff.model_validate(store.get_raw("reproduce")),
            )
        if node_key == "report":
            if task.task_type == "discovery":
                # discovery 文档节点不吃单例 audit；权威结论来自 finalize
                return ReportInput(
                    profile=ProfileHandoff.model_validate(store.get_raw("profile")),
                    env_ready=EnvReadyHandoff.model_validate(store.get_raw("env_ready")),
                    audit=AuditHandoff.model_validate({}),
                    reproduce=ReproduceHandoff.model_validate({}),
                    vulnerability_description=task.vulnerability_description or "",
                    project_address=task.project_address,
                    expected_verdict=(store.get_raw("finalize") or {}).get(
                        "analysis_verdict"
                    ),
                )
            repro_raw = dict(store.get_raw("reproduce"))
            repro_raw.pop("report_data", None)
            finalize = store.get_raw("finalize")
            return ReportInput(
                profile=ProfileHandoff.model_validate(store.get_raw("profile")),
                env_ready=EnvReadyHandoff.model_validate(store.get_raw("env_ready")),
                audit=AuditHandoff.model_validate(store.get_raw("audit")),
                reproduce=ReproduceHandoff.model_validate(repro_raw),
                vulnerability_description=task.vulnerability_description or "",
                project_address=task.project_address,
                expected_verdict=finalize.get("analysis_verdict"),
            )
        raise KeyError(f"无法组装未知节点 Input: {node_key}")

    @staticmethod
    def from_previous_outputs(
        node_key: str,
        previous: dict[str, dict],
        *,
        vulnerability_description: str = "",
        project_address: str = "",
        project_ref: str | None = None,
        project_ref_type: str | None = None,
        clone_depth: int | None = 1,
        source_type: str = "git",
        host_workdir: str = "",
        source_path: str = "",
    ) -> Any:
        """单测 / 遗留调用：从 previous_outputs 组装（节点 execute 缺省入参时）。"""
        store = HandoffStore()
        for k, v in (previous or {}).items():
            store.set(k, v)
        return InputAssembler.assemble(
            node_key,
            store,
            TaskScalars(
                project_address=project_address,
                project_ref=project_ref,
                project_ref_type=project_ref_type,
                clone_depth=clone_depth,
                source_type=source_type,
                vulnerability_description=vulnerability_description,
                host_workdir=host_workdir,
                source_path=source_path,
            ),
        )

    @staticmethod
    def dump_for_persistence(node_input: Any) -> dict[str, Any]:
        if hasattr(node_input, "model_dump"):
            return node_input.model_dump(mode="json")
        return dict(node_input)
