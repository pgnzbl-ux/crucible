"""节点 3 白盒审计(AI)— 吃源码+漏洞描述,产出 kill_chain + gate_verdict。"""
from __future__ import annotations

from typing import Any

from .base import NodeContext


class AuditNode:
    node_index = 3
    node_key = "audit"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import run_ai_node

        input_json = {
            "source_path": ctx.source_path,
            "vulnerability_description": ctx.vulnerability_description,
            "profile": ctx.previous_outputs.get("profile", {}),
        }
        return await run_ai_node(
            node_key="audit",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
        )
