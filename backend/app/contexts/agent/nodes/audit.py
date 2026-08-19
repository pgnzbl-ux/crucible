"""节点 3 白盒审计(AI)— 吃源码+漏洞描述,产出 kill_chain + gate_verdict。"""
from __future__ import annotations

from typing import Any

from .base import NodeContext, repo_dirname_from_outputs, workspace_repo_path


class AuditNode:
    node_index = 3
    node_key = "audit"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import run_ai_node_with_shape_retry

        src = ctx.previous_outputs.get("source", {})
        repo = src.get("repo_dirname") or repo_dirname_from_outputs(ctx.previous_outputs)
        input_json = {
            "source_path": src.get("workspace_path") or workspace_repo_path(repo),
            "vulnerability_description": ctx.vulnerability_description,
            "profile": ctx.previous_outputs.get("profile", {}),
        }
        return await run_ai_node_with_shape_retry(
            node_key="audit",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
        )
