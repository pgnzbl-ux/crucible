"""节点 5 报告生成(AI)— 吃全部前序 output,产出 8 节 report_data + final_verdict。"""
from __future__ import annotations

from typing import Any

from .base import NodeContext


class ReportNode:
    node_index = 5
    node_key = "report"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import run_ai_node

        input_json = {
            "profile": ctx.previous_outputs.get("profile", {}),
            "env_ready": ctx.previous_outputs.get("env_ready", {}),
            "audit": ctx.previous_outputs.get("audit", {}),
            "reproduce": ctx.previous_outputs.get("reproduce", {}),
            "vulnerability_description": ctx.vulnerability_description,
            "project_address": ctx.project_address,
        }
        return await run_ai_node(
            node_key="report",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
        )
