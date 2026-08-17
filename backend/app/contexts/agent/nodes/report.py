"""节点 5 报告落库 — 唯一文档作者：始终跑 AI，按权威 verdict 写漏洞报告或验证记录。"""
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
        from app.contexts.agent.ai_runner import (
            authoritative_verdict,
            document_kind_for_verdict,
            run_ai_node,
        )

        repro = dict(ctx.previous_outputs.get("reproduce") or {})
        repro.pop("report_data", None)
        audit = ctx.previous_outputs.get("audit") or {}
        expected = authoritative_verdict(repro, audit)
        if expected is None:
            raise RuntimeError("report 节点缺少权威 verdict")
        kind = document_kind_for_verdict(expected)

        input_json = {
            "profile": ctx.previous_outputs.get("profile", {}),
            "env_ready": ctx.previous_outputs.get("env_ready", {}),
            "audit": audit,
            "reproduce": repro,
            "vulnerability_description": ctx.vulnerability_description,
            "project_address": ctx.project_address,
            "expected_verdict": expected,
            "document_kind": kind,
        }
        output = await run_ai_node(
            node_key="report",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
        )
        final = output.get("final_verdict")
        if final != expected:
            raise RuntimeError(f"verdict 漂移: expected={expected} got={final}")
        output["authored_by"] = "reporter"
        return output
