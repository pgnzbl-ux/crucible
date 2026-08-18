"""6 节点编排器 — 驱动节点循环 + 分支出口 + 断点续跑。

替代旧 _run_analysis 的单次大调用。每节点:
  1. 查 NodeRun,已 completed → 复用 output_json,跳过执行
  2. 代码节点(0 source)worker 内执行;节点 1 profile 默认规则引擎(低置信才起 agent-runner)
  3. 持久化 NodeRun(status, output_json)
  4. 分支出口检查(非 web / 误报 / 基础设施失败)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.task.models import NodeRun, Task, TaskRun
from .nodes.audit import AuditNode
from .nodes.base import NodeContext, repo_dirname_from_outputs, source_tree_present
from .nodes.env_ready import EnvReadyNode
from .nodes.profile import ProfileNode
from .nodes.report import ReportNode
from .nodes.reproduce import ReproduceNode
from .nodes.source import SourceNode

logger = logging.getLogger(__name__)


async def _is_cancelled(session: AsyncSession, task: Task, run: TaskRun) -> bool:
    """以库里最新状态为准（API 取消走另一个 session）。"""
    await session.refresh(task)
    await session.refresh(run)
    return task.status == "cancelled" or run.status == "cancelled"


def _cancelled_result() -> dict[str, Any]:
    return {"status": "cancelled", "verdict": None}


# 节点执行顺序(索引 0-5)
NODE_ORDER = [
    SourceNode(),     # 0
    ProfileNode(),    # 1
    EnvReadyNode(),   # 2
    AuditNode(),      # 3
    ReproduceNode(),  # 4
    ReportNode(),     # 5
]


async def _get_or_create_node_run(
    session: AsyncSession,
    run_id: str,
    task_id: str,
    node_index: int,
    node_key: str,
) -> NodeRun:
    """取现有 NodeRun(断点续跑)或建新的。"""
    result = await session.execute(
        select(NodeRun).where(
            NodeRun.run_id == run_id, NodeRun.node_index == node_index
        )
    )
    nr = result.scalar_one_or_none()
    if nr:
        return nr
    nr = NodeRun(
        run_id=run_id, task_id=task_id, node_index=node_index, node_key=node_key,
        status="pending", input_json="{}",
    )
    session.add(nr)
    await session.flush()
    await session.refresh(nr)
    return nr


async def _skip_reproduce_if_gate_fail(
    session: AsyncSession,
    run_id: str,
    task_id: str,
    previous_outputs: dict[str, dict],
    on_node_event,
) -> str | None:
    """audit gate_fail → 跳过 reproduce,返回 false_positive;否则 None。"""
    gate = previous_outputs.get("audit", {}).get("gate_verdict")
    if gate != "fail":
        return None
    repro_nr = await _get_or_create_node_run(
        session, run_id, task_id, NODE_ORDER[4].node_index, NODE_ORDER[4].node_key
    )
    if repro_nr.status != "completed":
        repro_nr.status = "skipped"
        await session.commit()
    if on_node_event:
        await on_node_event("reproduce", "skipped", None)
    return "false_positive"


async def _skip_reproduce_after_uncertain_audit(
    session: AsyncSession,
    run_id: str,
    task_id: str,
    previous_outputs: dict[str, dict],
    on_node_event,
) -> bool:
    """audit uncertain → 跳过 reproduce(锁不住危害不打活靶),但 report 仍跑,
    产出 needs_review 验证记录说明为何待复核(已 completed 的 reproduce 不改)。"""
    gate = previous_outputs.get("audit", {}).get("gate_verdict")
    if gate != "uncertain":
        return False
    nr = await _get_or_create_node_run(
        session, run_id, task_id, NODE_ORDER[4].node_index, NODE_ORDER[4].node_key
    )
    if nr.status != "completed":
        nr.status = "skipped"
        await session.commit()
    if on_node_event:
        await on_node_event(NODE_ORDER[4].node_key, "skipped", None)
    return True


def _handoff_input(task: Task, source_path: str, previous_outputs: dict[str, dict]) -> dict[str, Any]:
    """执行前写入 NodeRun.input_json 的实际交接，不是永远的 {}。"""
    return {
        "project_address": task.project_address,
        "project_ref": task.project_ref,
        "vulnerability_description": task.vulnerability_description,
        "source_path": source_path,
        "previous": previous_outputs,
    }


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
    nr.error_message = msg[:1000]
    nr.finished_at = now
    run.status = "failed"
    run.error_message = msg[:500]
    run.finished_at = now
    task.status = "failed"
    await session.commit()
    return {"status": "failed", "error": msg, "node": node_key, "verdict": None}


async def _finish_after_report_fail(
    session: AsyncSession,
    task: Task,
    run: TaskRun,
    nr: NodeRun,
    previous_outputs: dict[str, dict],
    final_verdict: str | None,
    *,
    title: str,
    hint: str,
    detail: str,
) -> dict[str, Any] | None:
    """audit 已给出 B/D 出口时，报告失败不得把任务改成 failed。"""
    audit_gate = previous_outputs.get("audit", {}).get("gate_verdict")
    keep_false_positive = final_verdict == "false_positive" or audit_gate == "fail"
    keep_review = audit_gate == "uncertain"
    if not keep_false_positive and not keep_review:
        return None
    now = datetime.now(timezone.utc)
    nr.status = "failed"
    nr.error_message = detail[:1000]
    nr.finished_at = now
    run.error_message = f"报告生成失败（审计结论已保留）: {title}"[:500]
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
    previous_outputs: dict[str, dict],
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
        profile = previous_outputs.get("profile") or {}
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
) -> dict[str, Any]:
    """跑 6 节点编排。返回 {status, verdict, summary}。

    on_node_event: 可选回调 (node_key, status, output) → None,用于发 SSE。
    """
    task = await session.get(Task, task_id)
    run = await session.get(TaskRun, run_id)
    if not task or not run:
        return {"status": "failed", "error": "task/run 不存在"}

    previous_outputs: dict[str, dict] = {}
    final_verdict: str | None = None
    skipped_due_to_non_web = False

    for node in NODE_ORDER:
        nr = await _get_or_create_node_run(
            session, run_id, task_id, node.node_index, node.node_key
        )

        if await _is_cancelled(session, task, run):
            if nr.status in ("pending", "running"):
                nr.status = "cancelled"
                nr.finished_at = datetime.now(timezone.utc)
                await session.commit()
            return _cancelled_result()

        # 分支出口 A:节点 1 仅当 is_web 显式为 True 才进靶场；缺省/False 一律 skip
        if node.node_index >= 2 and previous_outputs.get("profile", {}).get("is_web") is not True:
            if nr.status != "completed":
                nr.status = "skipped"
                await session.flush()
            previous_outputs[node.node_key] = {}
            skipped_due_to_non_web = True
            if on_node_event:
                await on_node_event(node.node_key, "skipped", None)
            continue

        # 断点续跑:已完成 → 复用 output_json
        if nr.status == "completed":
            try:
                previous_outputs[node.node_key] = json.loads(nr.output_json or "{}")
            except json.JSONDecodeError:
                return await _fail_corrupt_output(session, task, run, nr, node.node_key)
            if node.node_key == "source" and not source_tree_present(
                host_workdir, previous_outputs.get("source")
            ):
                # 重试拷贝了 completed source，但工作区已被清掉 → 必须重拉
                nr.status = "pending"
                previous_outputs.pop("source", None)
                await session.flush()
            else:
                if node.node_key == "source":
                    source_path = previous_outputs["source"].get("project_path") or source_path
                if on_node_event:
                    await on_node_event(node.node_key, "reused", previous_outputs[node.node_key])
                if node.node_key == "audit":
                    verdict = await _skip_reproduce_if_gate_fail(
                        session, run_id, task_id, previous_outputs, on_node_event
                    )
                    if verdict:
                        final_verdict = verdict
                    await _skip_reproduce_after_uncertain_audit(
                        session, run_id, task_id, previous_outputs, on_node_event
                    )
                continue

        # 已被分支出口标 skipped(如 gate_fail 后的 reproduce)→ 不执行
        if nr.status == "skipped":
            previous_outputs[node.node_key] = {}
            if on_node_event:
                await on_node_event(node.node_key, "skipped", None)
            continue

        # 执行节点
        nr.input_json = json.dumps(
            _handoff_input(task, source_path, previous_outputs),
            ensure_ascii=False,
            default=str,
        )
        nr.status = "running"
        nr.started_at = datetime.now(timezone.utc)
        await session.commit()
        if on_node_event:
            await on_node_event(node.node_key, "running", None)

        ctx = NodeContext(
            task_id=task_id, run_id=run_id, host_workdir=host_workdir,
            source_path=source_path,
            vulnerability_description=task.vulnerability_description,
            project_address=task.project_address, project_ref=task.project_ref,
            previous_outputs=dict(previous_outputs), runner_env=runner_env,
            on_event=_stamp_ai_event(on_ai_event, node.node_key, nr.id),
            db_session=session,
            project_id=getattr(task, "project_id", None),
            owner_id=getattr(task, "owner_id", None),
            lab_id=getattr(task, "lab_id", None),
        )
        try:
            output = await node.execute(ctx)
            if await _is_cancelled(session, task, run):
                nr.status = "cancelled"
                nr.finished_at = datetime.now(timezone.utc)
                await session.commit()
                return _cancelled_result()
            nr.output_json = json.dumps(output, ensure_ascii=False, default=str)
            nr.status = "completed"
            nr.finished_at = datetime.now(timezone.utc)
            previous_outputs[node.node_key] = output
            if node.node_key == "source":
                source_path = output.get("project_path") or source_path
            await session.commit()
            if on_node_event:
                await on_node_event(node.node_key, "completed", output)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"节点 {node.node_key} 执行失败")
            if await _is_cancelled(session, task, run):
                nr.status = "cancelled"
                nr.finished_at = datetime.now(timezone.utc)
                await session.commit()
                return _cancelled_result()
            from app.contexts.agent.errors import format_agent_error, humanize_agent_error

            title, hint = humanize_agent_error(str(e))
            detail = format_agent_error(str(e), node_key=node.node_key)
            if node.node_key == "report":
                preserved = await _finish_after_report_fail(
                    session, task, run, nr, previous_outputs, final_verdict,
                    title=title, hint=hint, detail=detail,
                )
                if preserved:
                    await _maybe_upload_node_failure(
                        session, task, run, nr, host_workdir, previous_outputs, detail
                    )
                    if on_node_event:
                        await on_node_event(
                            node.node_key,
                            "failed",
                            {"error": title, "detail": str(e)[:300], "hint": hint},
                        )
                    return preserved
            nr.status = "failed"
            nr.error_message = detail[:1000]
            nr.finished_at = datetime.now(timezone.utc)
            run.status = "failed"
            run.error_message = f"节点 {node.node_key} 失败: {title}"[:500]
            run.finished_at = datetime.now(timezone.utc)
            task.status = "failed"
            await session.commit()
            await _maybe_upload_node_failure(
                session, task, run, nr, host_workdir, previous_outputs, detail
            )
            if on_node_event:
                await on_node_event(
                    node.node_key,
                    "failed",
                    {"error": title, "detail": str(e)[:300], "hint": hint},
                )
            return {
                "status": "failed",
                "error": title,
                "hint": hint,
                "node": node.node_key,
                "verdict": None,
            }

        # 分支出口 B:节点 3 audit gate_fail → 跳过节点 4,判误报
        # 分支出口 D:节点 3 audit uncertain → 跳过节点 4,report 仍产 needs_review 验证记录
        if node.node_key == "audit":
            verdict = await _skip_reproduce_if_gate_fail(
                session, run_id, task_id, previous_outputs, on_node_event
            )
            if verdict:
                final_verdict = verdict
            await _skip_reproduce_after_uncertain_audit(
                session, run_id, task_id, previous_outputs, on_node_event
            )

    # 节点 5 报告产出 final_verdict
    # 注意:若 audit 已判 false_positive(gate_fail),report 节点仍会执行(产出误报报告),
    # 但其 final_verdict 不应覆盖 audit 的误报结论 —— audit Gate 是权威判定。
    report_out = previous_outputs.get("report", {})
    if final_verdict != "false_positive" and report_out.get("final_verdict"):
        final_verdict = report_out["final_verdict"]

    # 分支出口 D 收尾:audit uncertain → 待复核。report 已产出 needs_review 验证记录,
    # 任务级 verdict 仍留空(未确认漏洞),但 report_data 落库让用户看到"为何待复核"。
    audit_gate = previous_outputs.get("audit", {}).get("gate_verdict")
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
            "project_path": previous_outputs.get("source", {}).get("project_path"),
        }

    # 收尾(非 web 与正常路径统一 completed;非 web 的 verdict 已为 None)
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
        "project_path": previous_outputs.get("source", {}).get("project_path"),
    }
