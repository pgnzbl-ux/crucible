"""节点编排器 — 依赖就绪渐进调度 + 分支出口 + 断点续跑(discovery-spec §4.2)。

有限就绪并行，不是通用 DAG 引擎：
- 某节点 requires 全部 terminal(completed/skipped 均满足) → 就绪并立即开跑；
- 同批就绪节点可并发；任一完成即写入 terminal 并解锁下游，不整波等待兄弟节点
  （故 scan_* 齐后 cluster/发现复核可与仍在跑的 env_ready 并行）；
- 验证任务是线性链，行为与旧顺序循环一致；
- 并发执行时每个节点持独立 session(node_session_factory)，主 session 只在准入/收尾使用。

skip 信号：NON_WEB / GATE_*(audit 后) / VERIFY_MODE / NO_DISPATCH_LEAD / LEAD_DRIVEN。
skip 视为 completed，满足下游 requires(验证任务 skip dispatch 后 audit 照常就绪)。
审计有入队线索时 DAG audit/reproduce 由 LEAD_DRIVEN skip，LeadWorker 排空后再聚合 report。

交接契约：docs/discovery-spec.md + contracts/
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.agent.contracts import (
    DEFAULT_PIPELINE,
    HandoffStore,
    InputAssembler,
    SkipWhen,
    TaskScalars,
)
from app.contexts.agent.errors import (
    RUN_ERROR_LOG_MAX,
    clip_error_log,
    format_agent_error,
    humanize_agent_error,
    node_error_log_from_output,
)
from app.contexts.task.models import NodeRun, Task, TaskRun

from .nodes.api_hunt import ApiHuntNode
from .nodes.api_inventory import ApiInventoryNode
from .nodes.audit import AuditNode
from .nodes.base import NodeContext, repo_dirname_from_outputs, source_tree_present
from .nodes.cluster import ClusterNode
from .nodes.dispatch import DispatchNode
from .nodes.env_ready import EnvReadyNode
from .nodes.profile import ProfileNode
from .nodes.report import ReportNode
from .nodes.reproduce import ReproduceNode
from .nodes.scan import GitleaksNode, OsvScanNode, SemgrepNode
from .nodes.screen import ScreenNode
from .nodes.source import SourceNode
from .nodes.triage import TriageNode

logger = logging.getLogger(__name__)

# 节点执行器（与 DEFAULT_PIPELINE 顺序对齐）
_NODE_EXECUTORS = {
    "source": SourceNode(),
    "profile": ProfileNode(),
    "scan_gitleaks": GitleaksNode(),
    "scan_osv": OsvScanNode(),
    "scan_semgrep": SemgrepNode(),
    "api_inventory": ApiInventoryNode(),
    "env_ready": EnvReadyNode(),
    "cluster": ClusterNode(),
    "api_hunt": ApiHuntNode(),
    "screen": ScreenNode(),
    "triage": TriageNode(),
    "dispatch": DispatchNode(),
    "audit": AuditNode(),
    "reproduce": ReproduceNode(),
    "report": ReportNode(),
}

# 兼容测试与外部引用：按 DEFAULT_PIPELINE 顺序的执行器列表
NODE_ORDER = [_NODE_EXECUTORS[spec.key] for spec in DEFAULT_PIPELINE]


def _validate_executor_registry() -> None:
    """执行器表必须与 pipeline 逐 key 对齐(单一事实来源 registry)。"""
    pipeline_keys = {spec.key for spec in DEFAULT_PIPELINE}
    executor_keys = set(_NODE_EXECUTORS)
    missing = pipeline_keys - executor_keys
    extra = executor_keys - pipeline_keys
    if missing:
        raise AssertionError(f"pipeline 节点缺少执行器: {sorted(missing)}")
    if extra:
        raise AssertionError(f"执行器表存在 pipeline 之外的节点: {sorted(extra)}")
    for key, node in _NODE_EXECUTORS.items():
        actual = getattr(node, "node_key", None)
        if actual != key:
            raise AssertionError(f"执行器 {key} 的 node_key 为 {actual!r}，与 registry 不一致")


_validate_executor_registry()


async def _is_cancelled(session: AsyncSession, task: Task, run: TaskRun) -> bool:
    """以库里最新状态为准（API 取消走另一个 session）。"""
    await session.refresh(task)
    await session.refresh(run)
    return task.status == "cancelled" or run.status == "cancelled"


async def _is_cancelled_by_id(session: AsyncSession, task_id: str, run_id: str) -> bool:
    """并发波内节点的取消检查：按 id 取最新状态，不共享主 session 对象。"""
    task = await session.get(Task, task_id)
    run = await session.get(TaskRun, run_id)
    if not task or not run:
        return False
    return task.status == "cancelled" or run.status == "cancelled"


def _cancelled_result() -> dict[str, Any]:
    return {"status": "cancelled", "verdict": None}


@dataclass(frozen=True)
class _TaskExecSnap:
    """并发节点执行用的 Task 标量快照，避免跨 session 碰 ORM。"""

    id: str
    vulnerability_description: str | None
    project_address: str
    project_ref: str | None
    project_ref_type: str | None
    clone_depth: int | None
    source_type: str
    project_id: str | None
    owner_id: str | None
    lab_id: str | None


def _task_exec_snap(task: Task) -> _TaskExecSnap:
    return _TaskExecSnap(
        id=task.id,
        vulnerability_description=task.vulnerability_description,
        project_address=task.project_address,
        project_ref=task.project_ref,
        project_ref_type=getattr(task, "project_ref_type", None),
        clone_depth=getattr(task, "clone_depth", None),
        source_type=getattr(task, "source_type", None) or "git",
        project_id=getattr(task, "project_id", None),
        owner_id=getattr(task, "owner_id", None),
        lab_id=getattr(task, "lab_id", None),
    )


def _task_scalars(task: Task, host_workdir: str, source_path: str) -> TaskScalars:
    return TaskScalars(
        project_address=task.project_address,
        project_ref=task.project_ref,
        project_ref_type=getattr(task, "project_ref_type", None),
        clone_depth=getattr(task, "clone_depth", None),
        source_type=getattr(task, "source_type", None) or "git",
        vulnerability_description=task.vulnerability_description,
        host_workdir=host_workdir,
        source_path=source_path,
        task_type=getattr(task, "task_type", None) or "verify",
    )


async def _lookup_node_run(
    session: AsyncSession, run_id: str, node_index: int
) -> NodeRun | None:
    result = await session.execute(
        select(NodeRun).where(
            NodeRun.run_id == run_id, NodeRun.node_index == node_index
        )
    )
    return result.scalar_one_or_none()


async def _get_or_create_node_run(
    session: AsyncSession,
    run_id: str,
    task_id: str,
    node_index: int,
    node_key: str,
) -> NodeRun:
    """取现有 NodeRun(断点续跑)或建新的。

    Celery acks_late 重投时同一 run 可能并行进编排；INSERT 撞
    idx_node_runs_run_idx 时复用已有行（与 ensure_report_for_run 同模式）。
    """
    nr = await _lookup_node_run(session, run_id, node_index)
    if nr:
        return nr
    nr = NodeRun(
        run_id=run_id, task_id=task_id, node_index=node_index, node_key=node_key,
        status="pending", input_json="{}",
    )
    try:
        async with session.begin_nested():
            session.add(nr)
            await session.flush()
        await session.refresh(nr)
        return nr
    except IntegrityError:
        found = await _lookup_node_run(session, run_id, node_index)
        if found is None:
            raise
        return found


def _stamp_ai_event(on_ai_event, node_key: str, node_run_id: str):
    """给 AI 事件补 node_key / node_run_id；on_event 是同步回调。"""
    if not on_ai_event:
        return None

    def _wrapped(event: Any) -> None:
        stamped = dict(event) if isinstance(event, dict) else {"value": event}
        stamped.setdefault("node_key", node_key)
        stamped.setdefault("node_run_id", node_run_id)
        on_ai_event(stamped)

    return _wrapped


async def _fail_corrupt_output(
    session: AsyncSession,
    task: Task,
    run: TaskRun,
    nr: NodeRun,
    node_key: str,
) -> dict[str, Any]:
    msg = f"节点 {node_key} 的 output_json 损坏，无法续跑"
    now = datetime.now(timezone.utc)
    nr.status = "failed"
    nr.error_message = clip_error_log(msg)
    nr.finished_at = now
    run.status = "failed"
    run.error_message = clip_error_log(msg, limit=RUN_ERROR_LOG_MAX)
    run.finished_at = now
    task.status = "failed"
    await session.commit()
    return {"status": "failed", "error": msg, "node": node_key, "verdict": None}


async def _finish_after_report_fail(
    session: AsyncSession,
    task: Task,
    run: TaskRun,
    nr: NodeRun,
    store: HandoffStore,
    final_verdict: str | None,
    *,
    title: str,
    hint: str,
    detail: str,
    verify_mode: bool = False,
) -> dict[str, Any] | None:
    """audit 已给出 B/D 出口时，报告失败不得把任务改成 failed。"""
    audit_gate = store.signals(verify_mode=verify_mode).gate_verdict
    keep_false_positive = final_verdict == "false_positive" or audit_gate == "fail"
    keep_review = audit_gate == "uncertain"
    if not keep_false_positive and not keep_review:
        return None
    now = datetime.now(timezone.utc)
    nr.status = "failed"
    nr.error_message = clip_error_log(detail)
    nr.finished_at = now
    run.error_message = clip_error_log(
        f"报告生成失败（审计结论已保留）: {title}", limit=RUN_ERROR_LOG_MAX
    )
    run.finished_at = now
    run.status = "completed"
    if keep_review:
        task.verdict = None
        task.status = "needs_review"
        await session.commit()
        return {
            "status": "needs_review",
            "verdict": None,
            "error": title,
            "hint": hint,
            "node": "report",
        }
    task.verdict = "false_positive"
    task.status = "completed"
    await session.commit()
    return {
        "status": "completed",
        "verdict": "false_positive",
        "error": title,
        "hint": hint,
        "node": "report",
    }


async def _maybe_upload_node_failure(
    session: AsyncSession,
    task: Task,
    run: TaskRun,
    nr: NodeRun,
    host_workdir: str,
    store: HandoffStore,
    detail: str,
) -> None:
    """节点最终 failed 后上传 node_run 包。Mock / 上传失败不改终态。"""
    from pathlib import Path

    from app.core.config import get_settings

    if not get_settings().claude_agent_sdk_enabled:
        return
    if not getattr(task, "owner_id", None):
        return
    try:
        from app.contexts.agent.node_failure import (
            classify_node_error,
            infer_failed_stage,
            pack_node_run_bundle,
            snapshot_attempt,
        )
        from app.contexts.task.repository import TaskRepository
        from app.contexts.task.service import TaskService

        stage = infer_failed_stage(detail)
        error_class = classify_node_error(failed_stage=stage, error_text=detail)
        attempts_root = Path(host_workdir) / ".node-failure" / nr.node_key / "attempts"
        if not attempts_root.is_dir():
            snapshot_attempt(
                host_workdir,
                nr.node_key,
                max(nr.attempt or 1, 1),
                platform_error=detail,
                copy_vuln_env=nr.node_key == "env_ready",
            )
        attempt_count = 0
        if attempts_root.is_dir():
            attempt_count = sum(1 for p in attempts_root.iterdir() if p.is_dir())
        attempt_count = max(attempt_count, nr.attempt or 1)
        profile = store.get_raw("profile")
        language = profile.get("language") if isinstance(profile.get("language"), str) else None
        manifest = {
            "schema_version": 1,
            "task_id": task.id,
            "run_id": run.id,
            "node_run_id": nr.id,
            "node_key": nr.node_key,
            "owner_id": task.owner_id,
            "error_class": error_class,
            "failed_stage": stage,
            "attempt_count": attempt_count,
            "language": language,
        }
        bundle = pack_node_run_bundle(host_workdir, nr.node_key, manifest)
        await TaskService(TaskRepository(session)).record_node_run_failure(
            owner_id=task.owner_id,
            task_id=task.id,
            run_id=run.id,
            node_run_id=nr.id,
            node_key=nr.node_key,
            error_class=error_class,
            failed_stage=stage,
            language=language,
            attempt_count=attempt_count,
            bundle=bundle,
        )
        await session.commit()
    except Exception:
        logger.warning(
            "node_run 包上传失败 node=%s task=%s",
            nr.node_key,
            task.id,
            exc_info=True,
        )


def _evaluate_skip(
    spec, store: HandoffStore, verify_mode: bool
) -> SkipWhen | None:
    """就绪节点的分支出口评估：返回命中的信号(按声明顺序)。"""
    signals = store.signals(verify_mode=verify_mode)
    for signal in spec.skip_when:
        if signal == SkipWhen.NON_WEB and signals.non_web:
            return signal
        if signal == SkipWhen.VERIFY_MODE and verify_mode:
            return signal
        if signal == SkipWhen.NO_DISPATCH_LEAD and signals.no_dispatch_lead:
            return signal
        if signal == SkipWhen.LEAD_DRIVEN and signals.lead_driven:
            return signal
        if signal == SkipWhen.GATE_FAIL and signals.gate_verdict == "fail":
            return signal
        if signal == SkipWhen.GATE_UNCERTAIN and signals.gate_verdict == "uncertain":
            return signal
    return None


def compute_ready(pipeline, terminal: set[str]) -> list:
    """就绪集：requires 全部 terminal 且自身未 terminal。纯函数，供契约测试。"""
    return [
        spec for spec in pipeline
        if spec.key not in terminal
        and all(dep in terminal for dep in spec.requires)
    ]


def _require_data_missing(spec, store: HandoffStore, scalars: TaskScalars) -> bool:
    """数据前提(discovery-spec §4.2.2)：点分路径为空即缺失。

    根映射由 require_data 前缀派生（"task." → 任务标量，其余 → 对应节点 output），
    不再硬编码 source/profile/cluster。
    """
    if not spec.require_data:
        return False
    task_root: dict[str, Any] = {
        "project_address": scalars.project_address,
        "vulnerability_description": scalars.vulnerability_description,
    }
    for dotted in spec.require_data:
        parts = dotted.split(".")
        root = parts[0]
        value: Any = task_root if root == "task" else store.get_raw(root)
        for part in parts[1:]:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(part)
        if value in (None, "", [], {}):
            return True
    return False


class _NodeFailure(Exception):
    """波内节点执行失败(携带原始异常)，由主循环统一收尾。"""

    def __init__(self, spec_key: str, error: BaseException):
        super().__init__(f"{spec_key}: {error}")
        self.spec_key = spec_key
        self.error = error


class _NodeCancelled(Exception):
    """波内节点执行时任务被取消。"""


async def _execute_one(
    *,
    spec,
    node,
    nr_id: str,
    node_input,
    task_snap: _TaskExecSnap,
    run_id: str,
    host_workdir: str,
    source_path: str,
    runner_env: dict[str, str],
    on_ai_event,
    node_session: AsyncSession | None,
    session_factory=None,
    previous_outputs: dict | None = None,
) -> dict[str, Any]:
    """执行单个节点并落 NodeRun 终态。并发波用独立 session，顺序路径共享主 session。"""
    owns_session = node_session is None
    ns = node_session or (session_factory() if session_factory else None)
    if ns is None:
        raise RuntimeError("并发执行必须提供 node_session 或 session_factory")
    try:
        nr = await ns.get(NodeRun, nr_id)
        ctx = NodeContext(
            task_id=task_snap.id, run_id=run_id, host_workdir=host_workdir,
            source_path=source_path,
            vulnerability_description=task_snap.vulnerability_description,
            project_address=task_snap.project_address,
            project_ref=task_snap.project_ref,
            project_ref_type=task_snap.project_ref_type,
            clone_depth=task_snap.clone_depth,
            source_type=task_snap.source_type,
            previous_outputs=previous_outputs or {}, runner_env=runner_env,
            node_run_id=nr_id,
            on_event=_stamp_ai_event(on_ai_event, spec.key, nr_id),
            db_session=ns,
            session_factory=session_factory,
            project_id=task_snap.project_id,
            owner_id=task_snap.owner_id,
            lab_id=task_snap.lab_id,
        )
        output = await node.execute(ctx, node_input)
        if await _is_cancelled_by_id(ns, task_snap.id, run_id):
            nr.status = "cancelled"
            nr.finished_at = datetime.now(timezone.utc)
            await ns.commit()
            raise _NodeCancelled()
        nr.output_json = json.dumps(output, ensure_ascii=False, default=str)
        nr.status = "completed"
        logged = node_error_log_from_output(output if isinstance(output, dict) else None)
        if logged:
            nr.error_message = logged
        nr.finished_at = datetime.now(timezone.utc)
        await ns.commit()
        return output
    except _NodeCancelled:
        raise
    except Exception as e:  # noqa: BLE001
        with contextlib.suppress(Exception):
            await ns.rollback()
            nr = await ns.get(NodeRun, nr_id)
            if nr is not None and nr.status != "completed":
                nr.status = "failed"
                nr.error_message = clip_error_log(f"{e}\n\n{traceback.format_exc()}")
                nr.finished_at = datetime.now(timezone.utc)
                await ns.commit()
        raise _NodeFailure(spec.key, e) from e
    finally:
        if owns_session and session_factory is not None:
            with contextlib.suppress(Exception):
                await ns.close()


async def _drain_leads_before_report(
    *,
    session: AsyncSession,
    session_factory,
    task_id: str,
    run_id: str,
    host_workdir: str,
    source_path: str,
    runner_env: dict[str, str],
    store: HandoffStore,
    on_ai_event,
    lab_id: str | None = None,
) -> None:
    """排空本任务 Redis 终认队列（有限并发），再让聚合 report 就绪。"""
    from app.contexts.agent.lead_worker import drain_lead_queue
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    profile = store.get_raw("profile")
    env_ready = store.get_raw("env_ready") or None
    if not env_ready:
        env_ready = None

    runtime = await SettingsService(SettingsRepository(session)).get_runtime_settings()

    factory = session_factory
    if factory is None:
        # 单测/降级：用绑定同一 engine 的临时 factory
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(
            session.bind, class_=AsyncSession, expire_on_commit=False,
        )

    await drain_lead_queue(
        session_factory=factory,
        task_id=task_id,
        host_workdir=host_workdir,
        source_path=source_path,
        runner_env=runner_env,
        source=store.get_raw("source"),
        profile=profile,
        env_ready=env_ready,
        on_event=on_ai_event,
        lab_id=lab_id,
        # 降级工厂与主 session 共享单连接：额外 session 会破坏主事务，跳过回收
        allow_reclaim=session_factory is not None,
        concurrency=runtime.lead_verify_per_task,
    )
    # 主 session 可能被旁路更新；刷新 LeadRun 可见性
    await session.commit()


async def _complete_discovery_aggregate_report(
    session: AsyncSession, run_id: str, nr_id: str,
) -> dict[str, Any]:
    """discovery 聚合报告：只把 confirmed/partial 写入漏洞清单，零确认仍出报告。"""
    from app.contexts.agent.lead_worker import (
        build_discovery_report_from_leads,
        load_lead_runs,
    )

    leads = await load_lead_runs(session, run_id)
    denoise: dict[str, Any] = {}
    cluster_nr = (await session.execute(
        select(NodeRun).where(
            NodeRun.run_id == run_id, NodeRun.node_key == "cluster",
        )
    )).scalar_one_or_none()
    if cluster_nr and cluster_nr.output_json:
        try:
            raw_out = json.loads(cluster_nr.output_json) if isinstance(
                cluster_nr.output_json, str
            ) else cluster_nr.output_json
            if isinstance(raw_out, dict):
                denoise = {
                    "finding_count": raw_out.get("finding_count"),
                    "dropped_c_count": raw_out.get("dropped_c_count"),
                    "dropped_c_by_engine": raw_out.get("dropped_c_by_engine"),
                    "group_count": raw_out.get("group_count"),
                    "bypass_count": raw_out.get("bypass_count"),
                }
        except (TypeError, json.JSONDecodeError):
            denoise = {}
    output = build_discovery_report_from_leads(leads, denoise=denoise) or {
        "final_verdict": None,
        "report_data": None,
        "empty_aggregate": True,
        "authored_by": "discovery_aggregate",
    }
    nr = await session.get(NodeRun, nr_id)
    if nr is not None:
        nr.output_json = json.dumps(output, ensure_ascii=False, default=str)
        nr.status = "completed"
        nr.finished_at = datetime.now(timezone.utc)
        await session.flush()
    return output


async def _mark_inflight_cancelled(session: AsyncSession, run_id: str) -> None:
    await session.execute(
        update(NodeRun)
        .where(NodeRun.run_id == run_id, NodeRun.status.in_(("pending", "running")))
        .values(status="cancelled", finished_at=datetime.now(timezone.utc))
    )
    await session.commit()


async def run_orchestration(
    *,
    task_id: str,
    run_id: str,
    session: AsyncSession,
    host_workdir: str,
    source_path: str,
    runner_env: dict[str, str],
    on_node_event=None,
    on_ai_event=None,
    node_session_factory=None,
) -> dict[str, Any]:
    """按依赖就绪渐进调度跑节点编排。返回 {status, verdict, summary}。

    on_node_event: 可选回调 (node_key, status, output) → None,用于发 SSE。
    node_session_factory: 并发时每节点独立 session 的工厂；
    缺省(单测)时退化为顺序执行、共享主 session。
    有工厂时任一节点完成即解锁下游，不整波等待兄弟节点。
    """
    task = await session.get(Task, task_id)
    run = await session.get(TaskRun, run_id)
    if not task or not run:
        return {"status": "failed", "error": "task/run 不存在"}

    verify_mode = (getattr(task, "task_type", None) or "verify") == "verify"
    task_snap = _task_exec_snap(task)
    store = HandoffStore()
    final_verdict: str | None = None
    skipped_due_to_non_web = False
    terminal: set[str] = set()
    # 并发在飞：Task → (spec, node, nr_id, node_input, previous_outputs)
    inflight: dict[asyncio.Task, tuple] = {}

    def _inflight_keys() -> set[str]:
        return {info[0].key for info in inflight.values()}

    async def _cancel_inflight() -> set[str]:
        """取消所有在飞任务，返回被连带取消的 node_key。"""
        if not inflight:
            return set()
        pending = list(inflight.keys())
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        keys = {inflight[t][0].key for t in pending}
        inflight.clear()
        return keys

    async def _spawn_prepared(prepared: list[tuple]) -> None:
        for spec, node, nr_id, node_input, prev in prepared:
            if spec.lead_driven_aggregate and not verify_mode:
                async def _agg(nr_id=nr_id):
                    async with node_session_factory() as ns:
                        try:
                            out = await _complete_discovery_aggregate_report(
                                ns, run_id, nr_id,
                            )
                            await ns.commit()
                            return out
                        except Exception as e:  # noqa: BLE001
                            raise _NodeFailure("report", e) from e
                t = asyncio.create_task(_agg())
            else:
                t = asyncio.create_task(_execute_one(
                    spec=spec, node=node, nr_id=nr_id, node_input=node_input,
                    task_snap=task_snap, run_id=run_id, host_workdir=host_workdir,
                    source_path=source_path, runner_env=runner_env,
                    on_ai_event=on_ai_event, node_session=None,
                    session_factory=node_session_factory,
                    previous_outputs=prev,
                ))
            inflight[t] = (spec, node, nr_id, node_input, prev)

    while True:
        if await _is_cancelled(session, task, run):
            await _cancel_inflight()
            await _mark_inflight_cancelled(session, run_id)
            return _cancelled_result()

        ready = [
            spec for spec in compute_ready(DEFAULT_PIPELINE, terminal)
            if spec.key not in _inflight_keys()
        ]
        if not ready and not inflight:
            break

        # 先清掉命中分支出口/数据前提缺失的节点(skip 视为 completed)
        scalars_probe = _task_scalars(task, host_workdir, source_path)
        wave: list = []
        early_fail: dict[str, Any] | None = None
        for spec in ready:
            hit = _evaluate_skip(spec, store, verify_mode)
            if hit is not None:
                nr = await _get_or_create_node_run(
                    session, run_id, task_id, spec.index, spec.key
                )
                if nr.status != "completed":
                    nr.status = "skipped"
                    await session.flush()
                store.set(spec.key, {})
                terminal.add(spec.key)
                if hit == SkipWhen.NON_WEB:
                    skipped_due_to_non_web = True
                if spec.skip_verdict.get(hit.value):
                    final_verdict = spec.skip_verdict[hit.value]
                if on_node_event:
                    await on_node_event(spec.key, "skipped", None)
                continue
            if _require_data_missing(spec, store, scalars_probe):
                if spec.on_missing_data == "fail":
                    await _cancel_inflight()
                    early_fail = await _finalize_node_failure(
                        session, task, run, spec, None, host_workdir, store,
                        final_verdict, RuntimeError(f"{spec.key} 数据前提缺失: {spec.require_data}"),
                        on_node_event, verify_mode,
                    )
                    break
                nr = await _get_or_create_node_run(
                    session, run_id, task_id, spec.index, spec.key
                )
                if nr.status != "completed":
                    nr.status = "skipped"
                    await session.flush()
                store.set(spec.key, {})
                terminal.add(spec.key)
                if on_node_event:
                    await on_node_event(spec.key, "skipped", None)
                continue
            wave.append(spec)
        if early_fail is not None:
            return early_fail

        # 断点续跑：已完成/已 skip 的节点直接复用
        executable: list = []
        for spec in wave:
            nr = await _get_or_create_node_run(
                session, run_id, task_id, spec.index, spec.key
            )
            if nr.status == "completed":
                try:
                    raw = json.loads(nr.output_json or "{}")
                except json.JSONDecodeError:
                    await _cancel_inflight()
                    return await _fail_corrupt_output(session, task, run, nr, spec.key)
                if spec.requires_workspace and not source_tree_present(
                    host_workdir, store.get_raw(spec.key) or raw
                ):
                    nr.status = "pending"
                    store.pop(spec.key)
                    await session.flush()
                    executable.append((spec, nr))
                    continue
                store.set(spec.key, raw)
                terminal.add(spec.key)
                if spec.updates_source_path:
                    source_path = store.get_raw(spec.key).get("project_path") or source_path
                if on_node_event:
                    await on_node_event(spec.key, "reused", store.get_raw(spec.key))
                continue
            if nr.status == "skipped":
                store.set(spec.key, {})
                terminal.add(spec.key)
                if on_node_event:
                    await on_node_event(spec.key, "skipped", None)
                continue
            executable.append((spec, nr))

        # 无新执行体：若有在飞则等完成；否则因 skip/reuse 重算就绪
        if not executable:
            if not inflight:
                continue
            done, _ = await asyncio.wait(
                inflight.keys(), return_when=asyncio.FIRST_COMPLETED,
            )
        else:
            # discovery 有入队线索：先排空 Lead 队列，再跑聚合 report(§4.4)
            if (
                any(spec.key == "report" for spec, _ in executable)
                and store.signals(verify_mode=verify_mode).lead_driven
            ):
                await _drain_leads_before_report(
                    session=session,
                    session_factory=node_session_factory,
                    task_id=task_id,
                    run_id=run_id,
                    host_workdir=host_workdir,
                    source_path=source_path,
                    runner_env=runner_env,
                    store=store,
                    on_ai_event=on_ai_event,
                    lab_id=task_snap.lab_id,
                )

            # 串行准备(主 session)：写 input_json / running，并组装 Input
            to_prepare = executable[:1] if node_session_factory is None else executable
            prepared: list[tuple] = []
            for spec, nr in to_prepare:
                scalars = _task_scalars(task, host_workdir, source_path)
                try:
                    node_input = InputAssembler.assemble(spec.key, store, scalars)
                except Exception as e:  # noqa: BLE001
                    await _cancel_inflight()
                    await _mark_inflight_cancelled(session, run_id)
                    return await _finalize_node_failure(
                        session, task, run, spec, nr.id, host_workdir, store,
                        final_verdict, e, on_node_event,
                    )
                nr.input_json = json.dumps(
                    InputAssembler.dump_for_persistence(node_input),
                    ensure_ascii=False,
                    default=str,
                )
                nr.status = "running"
                nr.started_at = datetime.now(timezone.utc)
                await session.commit()
                if on_node_event:
                    await on_node_event(spec.key, "running", None)
                previous_outputs = store.as_previous_outputs()
                prepared.append(
                    (spec, _NODE_EXECUTORS[spec.key], nr.id, node_input, previous_outputs)
                )

            if node_session_factory is None:
                spec, node, nr_id, node_input, prev = prepared[0]
                try:
                    if spec.lead_driven_aggregate and not verify_mode:
                        output = await _complete_discovery_aggregate_report(
                            session, run_id, nr_id,
                        )
                    else:
                        output = await _execute_one(
                            spec=spec, node=node, nr_id=nr_id, node_input=node_input,
                            task_snap=task_snap, run_id=run_id, host_workdir=host_workdir,
                            source_path=source_path, runner_env=runner_env,
                            on_ai_event=on_ai_event, node_session=session,
                            previous_outputs=prev,
                        )
                except _NodeCancelled:
                    await _mark_inflight_cancelled(session, run_id)
                    return _cancelled_result()
                except _NodeFailure as f:
                    await _mark_inflight_cancelled(session, run_id)
                    return await _finalize_node_failure(
                        session, task, run, spec, nr_id, host_workdir, store,
                        final_verdict, f.error, on_node_event,
                    )
                store.set(spec.key, output)
                terminal.add(spec.key)
                if spec.updates_source_path:
                    source_path = output.get("project_path") or source_path
                if on_node_event:
                    await on_node_event(spec.key, "completed", output)
                continue

            await _spawn_prepared(prepared)
            done, _ = await asyncio.wait(
                inflight.keys(), return_when=asyncio.FIRST_COMPLETED,
            )

        # 收尾已完成的在飞节点：先落成功，再处理取消/失败（与旧波次语义一致）
        batch: list[tuple] = []
        for t in done:
            info = inflight.pop(t)
            try:
                outcome: dict[str, Any] | _NodeFailure | _NodeCancelled = t.result()
            except _NodeCancelled:
                outcome = _NodeCancelled()
            except _NodeFailure as f:
                outcome = f
            except asyncio.CancelledError:
                outcome = _NodeCancelled()
            batch.append((info, outcome))

        for (spec, _node, _nr_id, _ni, _prev), outcome in batch:
            if isinstance(outcome, (_NodeFailure, _NodeCancelled)):
                continue
            store.set(spec.key, outcome)
            terminal.add(spec.key)
            if spec.updates_source_path:
                source_path = outcome.get("project_path") or source_path
            if on_node_event:
                await on_node_event(spec.key, "completed", outcome)

        cancel_hit = next(
            (item for item in batch if isinstance(item[1], _NodeCancelled)), None
        )
        if cancel_hit is not None:
            await _cancel_inflight()
            await _mark_inflight_cancelled(session, run_id)
            return _cancelled_result()

        fail_hit = next(
            (item for item in batch if isinstance(item[1], _NodeFailure)), None
        )
        if fail_hit is not None:
            (spec, _node, nr_id, _ni, _prev), outcome = fail_hit
            await _cancel_inflight()
            await _mark_inflight_cancelled(session, run_id)
            return await _finalize_node_failure(
                session, task, run, spec, nr_id, host_workdir, store,
                final_verdict, outcome.error, on_node_event,
            )

    previous_outputs = store.as_previous_outputs()
    report_out = store.get_raw("report")
    if final_verdict != "false_positive" and report_out.get("final_verdict"):
        final_verdict = report_out["final_verdict"]

    audit_gate = store.signals(verify_mode=verify_mode).gate_verdict
    if audit_gate == "uncertain":
        if await _is_cancelled(session, task, run):
            return _cancelled_result()
        task.verdict = None
        run.status = "completed"
        task.status = "needs_review"
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return {
            "status": "needs_review",
            "verdict": report_out.get("final_verdict") if report_out else None,
            "report_data": report_out.get("report_data") if report_out else None,
            "vulnerable_file": report_out.get("vulnerable_file") if report_out else None,
            "non_web": skipped_due_to_non_web,
            "repo_dirname": repo_dirname_from_outputs(previous_outputs),
            "project_path": store.get_raw("source").get("project_path"),
        }

    if await _is_cancelled(session, task, run):
        return _cancelled_result()
    task.verdict = final_verdict
    run.status = "completed"
    task.status = "completed"
    run.finished_at = datetime.now(timezone.utc)
    await session.commit()

    return {
        "status": "completed",
        "verdict": final_verdict,
        "report_data": report_out.get("report_data") if report_out else None,
        "cvss": report_out.get("cvss") if report_out else None,
        "vulnerable_file": report_out.get("vulnerable_file") if report_out else None,
        "poc": report_out.get("poc") if report_out else None,
        "non_web": skipped_due_to_non_web,
        "repo_dirname": repo_dirname_from_outputs(previous_outputs),
        "project_path": store.get_raw("source").get("project_path"),
    }


async def _finalize_node_failure(
    session: AsyncSession,
    task: Task,
    run: TaskRun,
    spec,
    nr_id: str,
    host_workdir: str,
    store: HandoffStore,
    final_verdict: str | None,
    error: BaseException,
    on_node_event,
    verify_mode: bool = False,
) -> dict[str, Any]:
    """节点失败收尾：沿用旧循环语义(报告失败保留审计结论 / 上传失败包)。"""
    await session.rollback()
    if await _is_cancelled(session, task, run):
        await _mark_inflight_cancelled(session, run_id=run.id)
        return _cancelled_result()

    title, hint = humanize_agent_error(str(error))
    detail = format_agent_error(str(error), node_key=spec.key)
    nr = await _lookup_node_run(session, run.id, spec.index)
    if nr is None:
        nr = await _get_or_create_node_run(session, run.id, task.id, spec.index, spec.key)

    if spec.failure_policy == "preserve_audit_verdict":
        preserved = await _finish_after_report_fail(
            session, task, run, nr, store, final_verdict,
            title=title, hint=hint, detail=detail, verify_mode=verify_mode,
        )
        if preserved:
            await _maybe_upload_node_failure(
                session, task, run, nr, host_workdir, store, detail
            )
            if on_node_event:
                await on_node_event(
                    spec.key,
                    "failed",
                    {"error": title, "detail": str(error)[:300], "hint": hint},
                )
            return preserved

    nr.status = "failed"
    nr.error_message = clip_error_log(detail)
    nr.finished_at = datetime.now(timezone.utc)
    run.status = "failed"
    run.error_message = clip_error_log(
        f"节点 {spec.key} 失败: {title}", limit=RUN_ERROR_LOG_MAX
    )
    run.finished_at = datetime.now(timezone.utc)
    task.status = "failed"
    await session.commit()
    await _maybe_upload_node_failure(
        session, task, run, nr, host_workdir, store, detail
    )
    if on_node_event:
        await on_node_event(
            spec.key,
            "failed",
            {"error": title, "detail": str(error)[:300], "hint": hint},
        )
    return {
        "status": "failed",
        "error": title,
        "hint": hint,
        "node": spec.key,
        "verdict": None,
    }
