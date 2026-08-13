"""6 节点编排器 — 驱动节点循环 + 分支出口 + 断点续跑。

替代旧 _run_analysis 的单次大调用。每节点:
  1. 查 NodeRun,已 completed → 复用 output_json,跳过执行
  2. 代码节点(0/1)worker 内执行;AI 节点(2/3/4/5)起 agent-runner
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
from .nodes.base import NodeContext
from .nodes.env_ready import EnvReadyNode
from .nodes.profile import ProfileNode
from .nodes.report import ReportNode
from .nodes.reproduce import ReproduceNode
from .nodes.source import SourceNode

logger = logging.getLogger(__name__)

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


async def run_orchestration(
    *,
    task_id: str,
    run_id: str,
    session: AsyncSession,
    host_workdir: str,
    source_path: str,
    runner_env: dict[str, str],
    on_node_event=None,
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

        # 分支出口 A:节点 1 判 is_web=False → 跳过节点 2-5
        if node.node_index >= 2 and not previous_outputs.get("profile", {}).get("is_web", True):
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
                previous_outputs[node.node_key] = {}
            if on_node_event:
                await on_node_event(node.node_key, "reused", previous_outputs[node.node_key])
            continue

        # 已被分支出口标 skipped(如 gate_fail 后的 reproduce)→ 不执行
        if nr.status == "skipped":
            previous_outputs[node.node_key] = {}
            if on_node_event:
                await on_node_event(node.node_key, "skipped", None)
            continue

        # 执行节点
        nr.status = "running"
        nr.started_at = datetime.now(timezone.utc)
        await session.flush()

        ctx = NodeContext(
            task_id=task_id, run_id=run_id, host_workdir=host_workdir,
            source_path=source_path,
            vulnerability_description=task.vulnerability_description,
            project_address=task.project_address, project_ref=task.project_ref,
            previous_outputs=dict(previous_outputs), runner_env=runner_env,
        )
        try:
            output = await node.execute(ctx)
            nr.output_json = json.dumps(output, ensure_ascii=False, default=str)
            nr.status = "completed"
            nr.finished_at = datetime.now(timezone.utc)
            previous_outputs[node.node_key] = output
            await session.commit()
            if on_node_event:
                await on_node_event(node.node_key, "completed", output)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"节点 {node.node_key} 执行失败")
            nr.status = "failed"
            nr.error_message = str(e)[:1000]
            nr.finished_at = datetime.now(timezone.utc)
            run.status = "failed"
            run.error_message = f"节点 {node.node_key} 失败: {e}"[:500]
            run.finished_at = datetime.now(timezone.utc)
            task.status = "failed"
            await session.commit()
            if on_node_event:
                await on_node_event(node.node_key, "failed", {"error": str(e)[:300]})
            return {
                "status": "failed", "error": str(e)[:500],
                "node": node.node_key, "verdict": None,
            }

        # 分支出口 B:节点 3 audit gate_fail → 跳过节点 4,判误报
        if node.node_key == "audit":
            gate = previous_outputs.get("audit", {}).get("gate_verdict")
            if gate == "fail":
                final_verdict = "false_positive"
                # 节点 4 标 skipped
                repro_nr = await _get_or_create_node_run(
                    session, run_id, task_id, NODE_ORDER[4].node_index, NODE_ORDER[4].node_key
                )
                if repro_nr.status != "completed":
                    repro_nr.status = "skipped"
                    await session.commit()

    # 节点 5 报告产出 final_verdict
    # 注意:若 audit 已判 false_positive(gate_fail),report 节点仍会执行(产出误报报告),
    # 但其 final_verdict 不应覆盖 audit 的误报结论 —— audit Gate 是权威判定。
    report_out = previous_outputs.get("report", {})
    if final_verdict != "false_positive" and report_out.get("final_verdict"):
        final_verdict = report_out["final_verdict"]

    # 收尾
    task.verdict = final_verdict
    if skipped_due_to_non_web and not report_out:
        # 非 web:completed 无 verdict
        run.status = "completed"
        task.status = "completed"
    else:
        run.status = "completed"
        task.status = "completed"
    run.finished_at = datetime.now(timezone.utc)
    await session.commit()

    return {
        "status": "completed",
        "verdict": final_verdict,
        "report_data": report_out.get("report_data") if report_out else None,
        "non_web": skipped_due_to_non_web,
    }
