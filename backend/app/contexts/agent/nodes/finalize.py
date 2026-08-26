"""finalize 节点 — 固化分析结论（analysis_verdict / analysis_status）。

报告渲染是消费者：discovery 在此只固化漏斗/线索终态；verify 从 audit/reproduce
推导权威 verdict。不得调用 AI 写文档。
"""
from __future__ import annotations

from typing import Any

from .base import NodeContext, emit_phase


class FinalizeNode:
    node_key = "finalize"

    @property
    def is_ai(self) -> bool:
        return False

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        task_type = getattr(ctx, "task_type", None) or "verify"
        emit_phase(ctx, "固化分析结论", phase=self.node_key)
        if task_type == "discovery":
            return await self._finalize_discovery(ctx)
        return self._finalize_verify(ctx, node_input)

    async def _finalize_discovery(self, ctx: NodeContext) -> dict[str, Any]:
        import json

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.contexts.agent.lead_worker import load_lead_runs
        from app.contexts.task.models import NodeRun

        factory = ctx.session_factory
        if factory is None:
            if ctx.db_session is None or ctx.db_session.bind is None:
                raise RuntimeError("finalize 需要 session_factory 或可用 db_session")
            factory = async_sessionmaker(
                ctx.db_session.bind, class_=AsyncSession, expire_on_commit=False,
            )

        async with factory() as session:
            leads = await load_lead_runs(session, ctx.run_id)
            denoise: dict[str, Any] = {}
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

        confirmed = [lr for lr in leads if lr.verdict in ("confirmed", "partial")]
        reachable = [lr for lr in leads if lr.verdict == "code_reachable"]
        needs_review = sum(
            1 for lr in leads
            if lr.status in ("failed", "skipped") or not lr.verdict
        )
        failed_leads = sum(1 for lr in leads if lr.status == "failed")
        analysis_verdict = (
            "confirmed" if any(lr.verdict == "confirmed" for lr in confirmed)
            else "partial" if confirmed else None
        )
        analysis_status = (
            "needs_review" if needs_review or failed_leads else "completed"
        )
        return {
            "analysis_verdict": analysis_verdict,
            "analysis_status": analysis_status,
            # 兼容编排器旧读取路径：与 analysis_verdict 同值
            "final_verdict": analysis_verdict,
            "lead_count": len(leads),
            "confirmed_count": len(confirmed),
            "code_reachable_count": len(reachable),
            "needs_review_count": needs_review,
            "denoise": denoise,
            "authored_by": "finalize",
        }

    def _finalize_verify(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import authoritative_verdict

        prev = ctx.previous_outputs or {}
        if node_input is not None:
            audit = (
                node_input.audit.model_dump(exclude_none=True)
                if getattr(node_input, "audit", None) is not None
                else (prev.get("audit") or {})
            )
            reproduce = (
                node_input.reproduce.model_dump(exclude_none=True)
                if getattr(node_input, "reproduce", None) is not None
                else (prev.get("reproduce") or {})
            )
        else:
            audit = prev.get("audit") or {}
            reproduce = prev.get("reproduce") or {}
        expected = authoritative_verdict(reproduce, audit)
        if expected is None:
            raise RuntimeError("finalize 缺少权威 verdict（audit/reproduce）")
        return {
            "analysis_verdict": expected,
            "analysis_status": (
                "needs_review" if expected == "needs_review" else "completed"
            ),
            "final_verdict": expected,
            "authored_by": "finalize",
        }
