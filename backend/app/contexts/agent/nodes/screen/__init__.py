"""screen 节点 — 轻量二审（T0 携带 + T1 规则 + T2 快审），无 Docker。

升级组保持 clustered，交给后续 triage（Agent）节点。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..base import NodeContext, emit_phase, task_run_cancelled
from ..triage import cascade
from ..triage.queue import order_groups, should_skip_llm
from ..triage.streamer import LeadStreamer


class ScreenNode:
    node_key = "screen"

    @property
    def is_ai(self) -> bool:
        return False

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "screen",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from app.contexts.finding.models import RawFinding
        from app.contexts.finding.service import FindingService
        from app.core.config import get_settings

        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp
        settings = get_settings()
        svc = FindingService(ctx.db_session)

        groups = await svc.list_groups(ctx.task_id, status="clustered")
        rep_ids = [
            g.representative_finding_id
            for g in groups
            if g.representative_finding_id
        ]
        sev_map: dict[str, str] = {}
        if rep_ids:
            rows = (
                await ctx.db_session.execute(
                    select(RawFinding.id, RawFinding.severity).where(
                        RawFinding.id.in_(rep_ids)
                    )
                )
            ).all()
            sev_map = {rid: (sev or "") for rid, sev in rows}

        ordered = order_groups(
            groups, severity_of=lambda g: sev_map.get(g.representative_finding_id, "")
        )
        queue = [g for g in ordered if not should_skip_llm(g)]
        skipped_llm = [g for g in groups if should_skip_llm(g)]
        for g in skipped_llm:
            await svc.mark_needs_review(g)

        candidate_ids = [g.id for g in queue]
        stats = cascade.TierStats()
        emit_phase(
            ctx,
            f"轻量待审 {len(queue)} 组（跳过 LLM {len(skipped_llm)}）",
            phase=self.node_key,
        )

        streamer = (
            LeadStreamer(ctx, settings, phase=self.node_key)
            if getattr(settings, "triage_stream_dispatch_enabled", True)
            else None
        )
        try:
            cancelled = await self._run_light(
                ctx, svc, queue, settings, stats, streamer=streamer,
            )
        except BaseException:
            if streamer is not None:
                await streamer.cancel_and_reap()
            raise

        if cancelled:
            if streamer is not None:
                await streamer.cancel_and_reap()
            await ctx.db_session.commit()
            return self._output(stats, len(skipped_llm), cancelled=True)

        if streamer is not None:
            await streamer.poll()
            if streamer.enqueued:
                emit_phase(
                    ctx,
                    f"轻量定案完成，等待 {streamer.enqueued} 条流式终认线索排空",
                    phase=self.node_key,
                )
            await streamer.join()
        await ctx.db_session.commit()
        verdict_counts = await self._verdict_counts(ctx, candidate_ids)
        await svc.discard_task_false_positives(ctx.task_id)
        emit_phase(
            ctx,
            f"轻量完成：携带 {stats.carried} · 规则 {stats.rule} · 快审 {stats.fast}"
            f" · 升级 {stats.escalated} · 跳过 {len(skipped_llm)}"
            f" · 可疑真洞 {verdict_counts['tp_count']} · 误报 {verdict_counts['fp_count']}",
            phase=self.node_key,
        )
        return {
            **self._output(stats, len(skipped_llm), cancelled=False),
            **verdict_counts,
        }

    async def _run_light(
        self,
        ctx: NodeContext,
        svc,
        queue: list,
        settings,
        stats: cascade.TierStats,
        *,
        streamer: LeadStreamer | None = None,
    ) -> bool:
        """T0–T2。cascade 关闭时整层跳过，全部留给 triage。返回 True=取消。"""
        if not getattr(settings, "triage_cascade_enabled", False):
            stats.escalated = len(queue)
            emit_phase(
                ctx,
                f"级联关闭，{len(queue)} 组直接升级 AI 二审",
                phase=self.node_key,
            )
            return False

        if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
            return True

        queue, stats.carried = await cascade.apply_carryover(
            svc, groups=queue, project_id=ctx.project_id, settings=settings,
        )
        await ctx.db_session.commit()

        queue, stats.rule = await cascade.apply_rule_preverdict(
            svc, groups=queue, settings=settings,
        )
        await ctx.db_session.commit()

        if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
            return True
        queue, stats.fast = await cascade.fast_screen(
            ctx, svc, groups=queue, settings=settings,
        )
        await ctx.db_session.commit()
        stats.escalated = len(queue)
        if streamer is not None:
            await streamer.poll()
        return False

    async def _verdict_counts(
        self, ctx: NodeContext, candidate_ids: list[str]
    ) -> dict[str, int]:
        from app.contexts.finding.models import AlertGroup

        counts = {"tp_count": 0, "fp_count": 0, "need_more_count": 0}
        if not candidate_ids:
            return counts
        rows = (
            await ctx.db_session.execute(
                select(AlertGroup.ai_verdict).where(AlertGroup.id.in_(candidate_ids))
            )
        ).scalars().all()
        for verdict in rows:
            if verdict == "tp":
                counts["tp_count"] += 1
            elif verdict == "fp":
                counts["fp_count"] += 1
            elif verdict == "need_more_context":
                counts["need_more_count"] += 1
        return counts

    def _output(
        self, stats: cascade.TierStats, skipped_llm: int, *, cancelled: bool,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "skipped_llm_count": skipped_llm,
            "carried_count": stats.carried,
            "rule_count": stats.rule,
            "fast_model_count": stats.fast,
            "escalated_count": stats.escalated,
            "budget_exhausted": stats.budget_exhausted,
        }
        if cancelled:
            out["status"] = "cancelled"
        return out
