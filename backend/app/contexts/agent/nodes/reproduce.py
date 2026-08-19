"""节点 4 复现验证(AI)— 吃靶场就绪全部产出 + 审计结果,产出复现证据+verdict。"""
from __future__ import annotations

from typing import Any

from .base import NodeContext, repo_dirname_from_outputs, workspace_repo_path


class ReproduceNode:
    node_index = 4
    node_key = "reproduce"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import (
            rewrite_url_for_agent_container,
            run_ai_node_with_shape_retry,
        )

        if ctx.lab_id:
            from app.contexts.lab.service import LabService

            svc = LabService(ctx.db_session)
            await svc.touch(ctx.lab_id)
            await svc.align_runtime_status(ctx.lab_id)

        env = ctx.previous_outputs.get("env_ready") or {}
        raw_url = env.get("target_url")
        if not raw_url:
            raise RuntimeError("复现节点缺少靶场就绪产出的 target_url，不能开跑")
        target_url = rewrite_url_for_agent_container(str(raw_url)) or str(raw_url)

        src = ctx.previous_outputs.get("source") or {}
        repo = src.get("repo_dirname") or repo_dirname_from_outputs(ctx.previous_outputs)
        input_json = {
            "source_path": src.get("workspace_path") or workspace_repo_path(repo),
            "target_url": target_url,
            "initial_creds": env.get("initial_creds") or {},
            "transport_shape": env.get("transport_shape") or {},
            "compose_path": env.get("compose_path"),
            "started_containers": env.get("started_containers") or [],
            "audit": ctx.previous_outputs.get("audit") or {},
            "vulnerability_description": ctx.vulnerability_description,
        }
        return await run_ai_node_with_shape_retry(
            node_key="reproduce",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
        )
