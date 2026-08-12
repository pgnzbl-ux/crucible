"""节点 4 复现验证(AI)— 吃靶标地址+审计结果,产出复现证据+verdict。"""
from __future__ import annotations

from typing import Any

from .base import NodeContext


class ReproduceNode:
    node_index = 4
    node_key = "reproduce"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import run_ai_node

        env = ctx.previous_outputs.get("env_ready", {})
        audit = ctx.previous_outputs.get("audit", {})
        input_json = {
            "target_url": env.get("target_url"),
            "transport_shape": env.get("transport_shape", {}),
            "audit": audit,
            "vulnerability_description": ctx.vulnerability_description,
        }
        return await run_ai_node(
            node_key="reproduce",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
        )
