"""节点拓扑声明 — 15 个能力节点，按任务模式裁剪子图。

权威契约：docs/discovery-spec.md。DEFAULT_PIPELINE 为一张图：
- 仓库审计(task_type=discovery)：全图；dispatch 入队；无队 NO_DISPATCH_LEAD skip 终认；
  有队 LEAD_DRIVEN skip DAG audit/reproduce；两种出口都生成聚合审计 report。
- 漏洞验证(task_type=verify)：只实例化 source/profile/env_ready/audit/reproduce/report。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SkipWhen(StrEnum):
    NON_WEB = "non_web"
    GATE_FAIL = "gate_fail"
    GATE_UNCERTAIN = "gate_uncertain"
    VERIFY_MODE = "verify_mode"
    NO_DISPATCH_LEAD = "no_dispatch_lead"
    LEAD_DRIVEN = "lead_driven"


@dataclass(frozen=True)
class NodeSpec:
    key: str
    index: int
    requires: tuple[str, ...]
    produces: str
    skip_when: frozenset[SkipWhen] = frozenset()
    # 数据前提(discovery-spec §4.2.2)：点分路径非空才就绪；缺失时按 on_missing_data 处理
    require_data: tuple[str, ...] = ()
    on_missing_data: str = "skip"  # skip | fail
    # 被某信号 skip 时顺带定稿的 verdict（如 reproduce 被 GATE_FAIL skip → false_positive）。
    # 键为 SkipWhen 的 value（"gate_fail" 等）；编排器 skip 分支统一应用，不再手写 helper。
    skip_verdict: dict[str, str] = field(default_factory=dict)
    # 断点续跑时校验工作区仍在（源码目录被清则降级 pending 重拉）
    requires_workspace: bool = False
    # 完成后把 output.project_path 发布为整条编排链的 source_path
    updates_source_path: bool = False
    # lead_driven 时代替节点执行器，改由聚合报告完成（discovery 主路径）
    lead_driven_aggregate: bool = False
    # 失败收尾策略：fail(默认) | preserve_audit_verdict(报告失败保留审计结论)
    failure_policy: str = "fail"


DEFAULT_PIPELINE: tuple[NodeSpec, ...] = (
    NodeSpec(
        key="source", index=0, requires=(), produces="source",
        requires_workspace=True, updates_source_path=True,
    ),
    NodeSpec(key="profile", index=1, requires=("source",), produces="profile"),
    NodeSpec(
        key="scan_gitleaks",
        index=2,
        requires=("source",),
        produces="scan_gitleaks",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
    ),
    NodeSpec(
        key="scan_osv",
        index=3,
        requires=("source",),
        produces="scan_osv",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
    ),
    NodeSpec(
        key="scan_semgrep",
        index=4,
        requires=("source", "profile"),
        produces="scan_semgrep",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
        require_data=("profile.semgrep_configs",),
        on_missing_data="skip",
    ),
    NodeSpec(
        key="api_inventory",
        index=5,
        requires=("source", "profile"),
        produces="api_inventory",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
    ),
    NodeSpec(
        key="env_ready",
        index=6,
        requires=("source", "profile", "dispatch"),
        produces="env_ready",
        skip_when=frozenset({SkipWhen.NON_WEB, SkipWhen.NO_DISPATCH_LEAD}),
    ),
    NodeSpec(
        key="cluster",
        index=7,
        requires=("scan_semgrep", "scan_gitleaks", "scan_osv"),
        produces="cluster",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
    ),
    NodeSpec(
        key="api_hunt",
        index=8,
        requires=("api_inventory",),
        produces="api_hunt",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
    ),
    NodeSpec(
        key="screen",
        index=9,
        requires=("cluster",),
        produces="screen",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
    ),
    NodeSpec(
        key="triage",
        index=10,
        requires=("screen",),
        produces="triage",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
    ),
    NodeSpec(
        key="dispatch",
        index=11,
        requires=("triage", "api_hunt"),
        produces="dispatch",
        skip_when=frozenset({SkipWhen.VERIFY_MODE}),
    ),
    # 审计模式 audit 等 dispatch；有队则 LEAD_DRIVEN skip（LeadWorker 跑）；
    # 验证模式 dispatch 被 skip 后照常就绪，不命中 LEAD_DRIVEN
    NodeSpec(
        key="audit",
        index=12,
        requires=("source", "profile", "dispatch"),
        produces="audit",
        skip_when=frozenset({
            SkipWhen.NO_DISPATCH_LEAD, SkipWhen.LEAD_DRIVEN,
        }),
    ),
    NodeSpec(
        key="reproduce",
        index=13,
        requires=("source", "env_ready", "audit"),
        produces="reproduce",
        skip_when=frozenset({
            SkipWhen.NON_WEB, SkipWhen.NO_DISPATCH_LEAD, SkipWhen.LEAD_DRIVEN,
            SkipWhen.GATE_FAIL, SkipWhen.GATE_UNCERTAIN,
        }),
        # audit gate 判 fail → 复现无意义，任务以 false_positive 收口（§5.8 出口 B）
        skip_verdict={"gate_fail": "false_positive"},
    ),
    NodeSpec(
        key="report",
        index=14,
        requires=("profile", "env_ready", "audit", "reproduce"),
        produces="report",
        # discovery 始终由聚合报告代替执行器（包括零线索）；报告失败不推翻审计结论
        lead_driven_aggregate=True,
        failure_policy="preserve_audit_verdict",
    ),
)

NODE_BY_KEY: dict[str, NodeSpec] = {spec.key: spec for spec in DEFAULT_PIPELINE}

# verify 保留 catalog index，但依赖只指向子图内节点。NodeRun 因此仍可
# 与历史数据/前端能力编号稳定对齐，同时不再产生九个伪 skip 行。
VERIFY_PIPELINE: tuple[NodeSpec, ...] = (
    NODE_BY_KEY["source"],
    NODE_BY_KEY["profile"],
    NodeSpec(
        key="env_ready", index=6, requires=("source", "profile"),
        produces="env_ready", skip_when=frozenset({SkipWhen.NON_WEB}),
    ),
    NodeSpec(
        key="audit", index=12, requires=("source", "profile"), produces="audit",
    ),
    NodeSpec(
        key="reproduce", index=13, requires=("source", "env_ready", "audit"),
        produces="reproduce",
        skip_when=frozenset({
            SkipWhen.NON_WEB, SkipWhen.GATE_FAIL, SkipWhen.GATE_UNCERTAIN,
        }),
        skip_verdict={"gate_fail": "false_positive"},
    ),
    NodeSpec(
        key="report", index=14,
        requires=("profile", "env_ready", "audit", "reproduce"),
        produces="report", failure_policy="preserve_audit_verdict",
    ),
)


def pipeline_for(task_type: str | None) -> tuple[NodeSpec, ...]:
    """返回任务真正执行的子图；None 仅兼容历史 verify 记录。"""
    if task_type == "discovery":
        return DEFAULT_PIPELINE
    if task_type in (None, "verify"):
        return VERIFY_PIPELINE
    raise ValueError(f"未知任务类型: {task_type}")


def ancestor_keys(pipeline: tuple[NodeSpec, ...], key: str) -> set[str]:
    """返回 key 的全部递归前置（不含自身）。"""
    by_key = {spec.key: spec for spec in pipeline}
    if key not in by_key:
        raise KeyError(f"未知子图节点: {key}")
    found: set[str] = set()
    stack = list(by_key[key].requires)
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        stack.extend(by_key[current].requires)
    return found


def descendant_keys(pipeline: tuple[NodeSpec, ...], key: str) -> set[str]:
    """返回从 key 开始必须失效的节点（含自身）。"""
    keys = {spec.key for spec in pipeline}
    if key not in keys:
        raise KeyError(f"未知子图节点: {key}")
    found = {key}
    changed = True
    while changed:
        changed = False
        for spec in pipeline:
            if spec.key not in found and any(dep in found for dep in spec.requires):
                found.add(spec.key)
                changed = True
    return found


def node_by_key(key: str, *, task_type: str | None = None) -> NodeSpec:
    try:
        if task_type is None:
            return NODE_BY_KEY[key]
        return {spec.key: spec for spec in pipeline_for(task_type)}[key]
    except KeyError as e:
        raise KeyError(f"未知节点: {key}") from e


def validate_pipeline(pipeline: tuple[NodeSpec, ...] = DEFAULT_PIPELINE) -> None:
    """拓扑自检：requires 引用存在的节点、无环、索引唯一且递增。"""
    keys = [s.key for s in pipeline]
    assert len(keys) == len(set(keys)), "节点 key 重复"
    indexes = [s.index for s in pipeline]
    assert indexes == sorted(set(indexes)), "node_index 必须唯一且递增"
    pipeline_keys = set(keys)
    for spec in pipeline:
        for dep in spec.requires:
            assert dep in pipeline_keys, f"{spec.key} 依赖子图外节点 {dep}"
    # 无环(Kahn)
    pending = {s.key: set(s.requires) for s in pipeline}
    resolved: set[str] = set()
    while pending:
        ready = [k for k, deps in pending.items() if deps <= resolved]
        if not ready:
            raise AssertionError(f"pipeline 存在环: {sorted(pending)}")
        for k in ready:
            pending.pop(k)
            resolved.add(k)
