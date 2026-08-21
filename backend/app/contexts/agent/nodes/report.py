"""节点 5 报告落库 — 唯一文档作者：始终跑 AI，按权威 verdict 写漏洞报告或验证记录。"""
from __future__ import annotations

from typing import Any

from app.contexts.agent.contracts import InputAssembler, ReportInput

from .base import NodeContext


class ReportNode:
    node_index = 5
    node_key = "report"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input: ReportInput | None) -> ReportInput:
        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "report",
            ctx.previous_outputs,
            vulnerability_description=ctx.vulnerability_description,
            project_address=ctx.project_address,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input: ReportInput | None = None) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import (
            authoritative_verdict,
            document_kind_for_verdict,
            run_ai_node_with_shape_retry,
        )

        inp = self._resolve_input(ctx, node_input)
        repro = inp.reproduce.model_dump(exclude_none=True)
        repro.pop("report_data", None)
        audit = inp.audit.model_dump(exclude_none=True)
        expected = inp.expected_verdict or authoritative_verdict(repro, audit)
        if expected is None:
            raise RuntimeError("report 节点缺少权威 verdict")
        kind = inp.document_kind or document_kind_for_verdict(expected)

        input_json = {
            "profile": inp.profile.model_dump(exclude_none=True),
            "env_ready": inp.env_ready.model_dump(exclude_none=True),
            "audit": audit,
            "reproduce": repro,
            "vulnerability_description": inp.vulnerability_description,
            "project_address": inp.project_address,
            "expected_verdict": expected,
            "document_kind": kind,
        }
        output = await run_ai_node_with_shape_retry(
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
        from app.contexts.agent.ai_runner import apply_poc_to_report_output

        repro_poc = repro.get("poc") if isinstance(repro.get("poc"), dict) else None
        output = apply_poc_to_report_output(output, repro_poc, expected)
        output["authored_by"] = "reporter"
        return output
