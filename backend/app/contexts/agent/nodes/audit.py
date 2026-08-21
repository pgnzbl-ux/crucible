"""节点 3 白盒审计(AI)— 吃源码+漏洞描述,产出 kill_chain + gate_verdict。"""
from __future__ import annotations

from typing import Any

from app.contexts.agent.contracts import AuditInput, InputAssembler

from .base import NodeContext, workspace_repo_path


class AuditNode:
    node_index = 3
    node_key = "audit"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input: AuditInput | None) -> AuditInput:
        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "audit",
            ctx.previous_outputs,
            vulnerability_description=ctx.vulnerability_description,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input: AuditInput | None = None) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import run_ai_node_with_shape_retry

        inp = self._resolve_input(ctx, node_input)
        src = inp.source
        input_json = {
            "source_path": src.workspace_path or workspace_repo_path(src.repo_dirname),
            "vulnerability_description": inp.vulnerability_description,
            "profile": inp.profile.model_dump(exclude_none=True),
        }
        return await run_ai_node_with_shape_retry(
            node_key="audit",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
        )
