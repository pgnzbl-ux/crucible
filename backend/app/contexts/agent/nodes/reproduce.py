"""节点 4 复现验证(AI)— 吃靶场就绪 Handoff + audit 子集,产出复现证据+verdict。"""
from __future__ import annotations

from typing import Any

from app.contexts.agent.contracts import InputAssembler, ReproduceInput

from .base import NodeContext, workspace_repo_path


class ReproduceNode:
    node_key = "reproduce"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input: ReproduceInput | None) -> ReproduceInput:
        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "reproduce",
            ctx.previous_outputs,
            vulnerability_description=ctx.vulnerability_description,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input: ReproduceInput | None = None) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import (
            rewrite_url_for_agent_container,
            run_ai_node_with_shape_retry,
        )

        if ctx.lab_id:
            from app.contexts.lab.service import LabService

            svc = LabService(ctx.db_session)
            await svc.touch(ctx.lab_id)
            await svc.align_runtime_status(ctx.lab_id)

        inp = self._resolve_input(ctx, node_input)
        env = inp.env_ready
        raw_url = env.target_url
        if not raw_url:
            raise RuntimeError("复现节点缺少靶场就绪产出的 target_url，不能开跑")
        target_url = rewrite_url_for_agent_container(str(raw_url)) or str(raw_url)

        src = inp.source
        input_json = {
            "source_path": src.workspace_path or workspace_repo_path(src.repo_dirname),
            "target_url": target_url,
            "initial_creds": env.initial_creds or {},
            "transport_shape": env.transport_shape or {},
            "compose_path": env.compose_path,
            "started_containers": env.started_containers or [],
            "audit": inp.audit.model_dump(exclude_none=True),
            "vulnerability_description": inp.vulnerability_description,
        }
        meta: dict[str, Any] = {}
        output = await run_ai_node_with_shape_retry(
            node_key="reproduce",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
            reproduce_scope=ctx.lab_id or ctx.task_id,
            meta_out=meta,
        )
        from app.contexts.agent.usage_ledger import record_node_usage

        await record_node_usage(ctx, "reproduce", meta)
        return output
