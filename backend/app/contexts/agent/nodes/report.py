"""报告节点 — 分析结论的文档消费者（不得改写权威 verdict）。

verify：AI 按 finalize.analysis_verdict 撰写漏洞报告/验证记录。
discovery：确定性聚合审计摘要（build_discovery_report_from_leads），不调模型。
"""
from __future__ import annotations

from typing import Any

from app.contexts.agent.contracts import InputAssembler, ReportInput

from .base import NodeContext, emit_phase


class ReportNode:
    node_key = "report"

    @property
    def is_ai(self) -> bool:
        # discovery 为代码聚合；verify 才起容器。编排器按 task 分流。
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
        task_type = getattr(ctx, "task_type", None) or "verify"
        if task_type == "discovery":
            return await self._execute_discovery(ctx)
        return await self._execute_verify(ctx, node_input)

    async def _execute_discovery(self, ctx: NodeContext) -> dict[str, Any]:
        import json

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.contexts.agent.lead_worker import (
            build_discovery_report_from_leads,
            load_lead_runs,
        )
        from app.contexts.task.models import NodeRun

        emit_phase(ctx, "聚合审计报告（消费 finalize）", phase=self.node_key)
        finalize = (ctx.previous_outputs or {}).get("finalize") or {}
        factory = ctx.session_factory
        if factory is None:
            if ctx.db_session is None or ctx.db_session.bind is None:
                raise RuntimeError("discovery report 需要 session_factory 或可用 db_session")
            factory = async_sessionmaker(
                ctx.db_session.bind, class_=AsyncSession, expire_on_commit=False,
            )

        async with factory() as session:
            leads = await load_lead_runs(session, ctx.run_id)
            denoise: dict[str, Any] = dict(finalize.get("denoise") or {})
            if not denoise:
                cluster_nr = (await session.execute(
                    select(NodeRun).where(
                        NodeRun.run_id == ctx.run_id, NodeRun.node_key == "cluster",
                    )
                )).scalar_one_or_none()
                if cluster_nr and cluster_nr.output_json:
                    try:
                        raw_out = json.loads(cluster_nr.output_json) if isinstance(
                            cluster_nr.output_json, str
                        ) else cluster_nr.output_json
                        if isinstance(raw_out, dict):
                            denoise = {
                                "finding_count": raw_out.get("finding_count"),
                                "dropped_c_count": raw_out.get("dropped_c_count"),
                                "dropped_c_by_engine": raw_out.get("dropped_c_by_engine"),
                                "group_count": raw_out.get("group_count"),
                                "bypass_count": raw_out.get("bypass_count"),
                            }
                    except (TypeError, json.JSONDecodeError):
                        denoise = {}

        output = build_discovery_report_from_leads(leads, denoise=denoise) or {
            "final_verdict": None,
            "analysis_verdict": None,
            "analysis_status": "completed",
            "report_data": None,
            "empty_aggregate": True,
        }
        # 权威结论只认 finalize；文档层不得改写
        analysis_verdict = finalize.get("analysis_verdict")
        analysis_status = finalize.get("analysis_status") or "completed"
        output["analysis_verdict"] = analysis_verdict
        output["analysis_status"] = analysis_status
        output["final_verdict"] = analysis_verdict
        output["authored_by"] = "discovery_report"
        return output

    async def _execute_verify(
        self, ctx: NodeContext, node_input: ReportInput | None,
    ) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import (
            authoritative_verdict,
            document_kind_for_verdict,
            run_ai_node_with_shape_retry,
        )

        inp = self._resolve_input(ctx, node_input)
        repro = inp.reproduce.model_dump(exclude_none=True)
        repro.pop("report_data", None)
        audit = inp.audit.model_dump(exclude_none=True)
        finalize = (ctx.previous_outputs or {}).get("finalize") or {}
        expected = (
            finalize.get("analysis_verdict")
            or inp.expected_verdict
            or authoritative_verdict(repro, audit)
        )
        if expected is None:
            raise RuntimeError("report 节点缺少权威 verdict（须先 finalize）")
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
        report_meta: dict[str, Any] = {}
        output = await run_ai_node_with_shape_retry(
            node_key="report",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
            meta_out=report_meta,
        )
        from app.contexts.agent.usage_ledger import record_node_usage

        await record_node_usage(ctx, "report", report_meta)
        final = output.get("final_verdict")
        if final != expected:
            raise RuntimeError(f"verdict 漂移: expected={expected} got={final}")
        from app.contexts.agent.ai_runner import apply_poc_to_report_output

        repro_poc = repro.get("poc") if isinstance(repro.get("poc"), dict) else None
        output = apply_poc_to_report_output(output, repro_poc, expected)
        output["authored_by"] = "reporter"
        output["analysis_verdict"] = expected
        output["analysis_status"] = (
            "needs_review" if expected == "needs_review" else "completed"
        )
        return output
