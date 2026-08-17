"""节点 5 报告落库 — 成功路径拷贝 reproduce 的 8 节 Markdown；仅误报路径跑 AI。"""
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
        from app.contexts.agent.ai_runner import _validate_report_data_markdown, run_ai_node

        repro = ctx.previous_outputs.get("reproduce") or {}
        rd = repro.get("report_data")
        if rd:
            ok, err = _validate_report_data_markdown(rd)
            if not ok:
                raise RuntimeError(err)
            return {
                "report_data": rd,
                "final_verdict": repro.get("verdict"),
                "cvss": repro.get("cvss"),
                "vulnerable_file": repro.get("vulnerable_file") or "",
                "authored_by": "reproduce",
            }

        input_json = {
            "profile": ctx.previous_outputs.get("profile", {}),
            "env_ready": ctx.previous_outputs.get("env_ready", {}),
            "audit": ctx.previous_outputs.get("audit", {}),
            "reproduce": repro,
            "vulnerability_description": ctx.vulnerability_description,
            "project_address": ctx.project_address,
        }
        output = await run_ai_node(
            node_key="report",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
        )
        output["authored_by"] = "reporter"
        return output
