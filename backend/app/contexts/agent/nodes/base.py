"""节点执行单元基类。

每个节点是一个 NodeExecutor,吃 NodeContext,产出 output_json dict。
代码节点(0/1)在 worker 进程内执行;AI 节点(2/3/4/5)由 ai_runner 起容器。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
