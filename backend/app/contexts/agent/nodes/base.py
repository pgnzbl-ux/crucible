"""节点执行单元基类。

每个节点是一个 NodeExecutor：吃 NodeContext + 声明的 NodeInput，产出 output_json dict。
代码节点(0 source)在 worker 进程内执行;AI 节点(1-5)由 ai_runner 起容器。

交接契约见 docs/discovery-spec.md §4.2/§6 与 app.contexts.agent.contracts。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def task_run_cancelled(
    session: AsyncSession, task_id: str, run_id: str | None = None,
) -> bool:
    """以库内最新状态判定取消，供长循环节点/lead drain 周期性自查。

    API 取消走另一个 session 提交，本 session 的对象缓存可能过期，
    所以必须发新鲜 SELECT（与 orchestrator._is_cancelled_by_id 同理）。
    """
    from app.contexts.task.models import Task, TaskRun

    task_status = (await session.execute(
        select(Task.status).where(Task.id == task_id)
    )).scalar_one_or_none()
    if task_status == "cancelled":
        return True
    if run_id is None:
        return False
    run_status = (await session.execute(
        select(TaskRun.status).where(TaskRun.id == run_id)
    )).scalar_one_or_none()
    return run_status == "cancelled"


def workspace_repo_path(repo_dirname: str | None) -> str:
    """容器内源码根：/workspace/{真实仓库名}，不再写死 project。"""
    name = (repo_dirname or "").strip().strip("/\\") or "project"
    return f"/workspace/{name}"


def repo_dirname_from_outputs(previous_outputs: dict[str, dict] | None) -> str | None:
    src = (previous_outputs or {}).get("source") or {}
    name = src.get("repo_dirname")
    return str(name) if name else None


def source_tree_present(host_workdir: str, source_output: dict | None = None) -> bool:
    """工作区里是否还有仓库目录。

    host_workdir 不存在时不做磁盘检查（单测常用假路径）；目录在但没有仓库则必须重拉。
    """
    if not host_workdir or not os.path.isdir(host_workdir):
        return True
    src = source_output or {}
    name = src.get("repo_dirname")
    if name and os.path.isdir(os.path.join(host_workdir, str(name))):
        return True
    for key in ("project_path", "source_path"):
        path = src.get(key)
        if isinstance(path, str) and os.path.isdir(path):
            try:
                if os.path.abspath(path) != os.path.abspath(host_workdir):
                    return True
            except OSError:
                return True
    skip = {".secrets"}
    try:
        entries = os.listdir(host_workdir)
    except OSError:
        return False
    for entry in entries:
        if entry.startswith(".") or entry in skip:
            continue
        if os.path.isdir(os.path.join(host_workdir, entry)):
            return True
    return False


@dataclass
class NodeContext:
    """节点执行上下文 — 平台能力（DB / lab / runner）；交接数据走 typed Input。"""
    task_id: str
    run_id: str
    host_workdir: str
    source_path: str
    vulnerability_description: str
    project_address: str
    project_ref: str | None
    project_ref_type: str | None = None
    clone_depth: int | None = 1
    source_type: str = "git"
    # 仅失败打包/env_ready 内部桥接兜底；节点业务路径必须读 typed Input（ctx.node_input）
    previous_outputs: dict[str, dict] = field(default_factory=dict)
    node_input: Any = None  # 当前节点 typed Input（env_ready 等在 execute 入口赋值）
    node_run_id: str | None = None  # 当前 NodeRun id(编排器注入；扫描节点落 ScanRun 用)
    runner_env: dict[str, str] = field(default_factory=dict)
    on_event: Callable[[dict], None] | None = None
    db_session: Any = None
    # 并发波会话工厂(编排器注入)：节点内部需要并行子任务时，每个子任务
    # 用独立 session，不得共用主 session。降级/单测可为 None → 串行执行。
    session_factory: Any = None
    project_id: str | None = None
    owner_id: str | None = None
    lab_id: str | None = None
    task_type: str = "verify"  # verify | discovery
    # 任务总时长预算的绝对截止(monotonic 秒)；编排器注入，None=无预算。
    # lead_verify 等 long-running 节点在认领新工作单元前自查，超限即软停转人工
    budget_deadline: float | None = None
    # 节点执行中修正上游 handoff（如 reproduce 复活靶场后回写 env_ready）
    updated_handoffs: dict[str, dict] = field(default_factory=dict)


def emit_phase(ctx: NodeContext, message: str, *, phase: str) -> None:
    """节点过程日志 → on_event → AgentEvent / SSE。编排器会再 stamp node_key。"""
    if ctx.on_event:
        ctx.on_event({"type": "phase.updated", "phase": phase, "message": message})


class NodeExecutor(Protocol):
    """节点执行协议。node_key 与 registry 对齐；index 由 registry 单一持有。"""
    node_key: str

    async def execute(self, ctx: NodeContext, node_input: Any = None) -> dict[str, Any]:
        """执行节点,返回 output_json（公开字段见 docs/discovery-spec.md §6）。"""
        ...

    @property
    def is_ai(self) -> bool:
        """AI 节点(需起容器)vs 代码节点(worker 内执行)。"""
        return False
