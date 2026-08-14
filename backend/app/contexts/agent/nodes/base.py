"""节点执行单元基类。

每个节点是一个 NodeExecutor,吃 NodeContext,产出 output_json dict。
代码节点(0 source)在 worker 进程内执行;AI 节点(1-5)由 ai_runner 起容器。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol
import os


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
    """节点执行上下文 — 持有任务信息 + 前序节点 output 累积。"""
    task_id: str
    run_id: str
    host_workdir: str
    source_path: str
    vulnerability_description: str
    project_address: str
    project_ref: str | None
    previous_outputs: dict[str, dict] = field(default_factory=dict)
    runner_env: dict[str, str] = field(default_factory=dict)
    on_event: Callable[[dict], None] | None = None
    db_session: Any = None
    project_id: str | None = None
    owner_id: str | None = None
    lab_id: str | None = None


class NodeExecutor(Protocol):
    """节点执行协议。"""
    node_index: int
    node_key: str

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        """执行节点,返回 output_json(按 spec §1.3 schema)。"""
        ...

    @property
    def is_ai(self) -> bool:
        """AI 节点(需起容器)vs 代码节点(worker 内执行)。"""
        return False
