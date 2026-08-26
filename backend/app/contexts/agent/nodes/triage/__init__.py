"""triage 节点 — AI 二审（T3 族代表 + Docker Agent + 族内传播）。

轻量 T0–T2 已由 screen 节点完成；本节点只消费仍为 clustered 的升级组。
cascade 关闭时回退为逐组全价 agent 串行。
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from sqlalchemy import select

from ..base import NodeContext, emit_phase, task_run_cancelled
from . import cascade
from .adjudicate import adjudicate_group
from .queue import order_groups, review_groups


class TriageNode:
    node_key = "triage"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "triage",
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

        # screen 已定案/转人工的组不再是 clustered；此处消费四路统一升级队列。
        groups = review_groups(
            await svc.list_groups(ctx.task_id, status="clustered")
        )
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

        queue = order_groups(
            groups, severity_of=lambda g: sev_map.get(g.representative_finding_id, "")
        )
        candidate_ids = [g.id for g in queue]
        stats = cascade.TierStats()
        # 优先 typed Input.screen；单测兜底 previous_outputs
        screen = getattr(inp, "screen", None)
        if screen is not None:
            stats.carried = int(getattr(screen, "carried_count", 0) or 0)
            stats.rule = int(getattr(screen, "rule_count", 0) or 0)
            stats.fast = int(getattr(screen, "fast_model_count", 0) or 0)
            skipped_llm = int(getattr(screen, "skipped_llm_count", 0) or 0)
        else:
            screen_out = (ctx.previous_outputs or {}).get("screen") or {}
            stats.carried = int(screen_out.get("carried_count") or 0)
            stats.rule = int(screen_out.get("rule_count") or 0)
            stats.fast = int(screen_out.get("fast_model_count") or 0)
            skipped_llm = int(screen_out.get("skipped_llm_count") or 0)
        stats.escalated = len(queue)

        emit_phase(
            ctx,
            f"AI 待审 {len(queue)} 组（轻量已处理携带/规则/快审）",
            phase=self.node_key,
        )

        cancelled = await self._run_agent(ctx, svc, queue, settings, stats)

        if cancelled:
            await ctx.db_session.commit()
            return self._output(stats, skipped_llm, 0, cancelled=True)

        await ctx.db_session.commit()
        leftover = await svc.mark_unaudited_for_review(ctx.task_id)
        await ctx.db_session.commit()
        verdict_counts = await self._verdict_counts(ctx, candidate_ids)
        await svc.discard_task_false_positives(ctx.task_id)
        emit_phase(
            ctx,
            f"完成：{stats.summary()} · 轻量跳过 {skipped_llm} · 残留 {leftover}"
            f" · 可疑真洞 {verdict_counts['tp_count']} · 误报 {verdict_counts['fp_count']}",
            phase=self.node_key,
        )
        return {
            **self._output(stats, skipped_llm, leftover, cancelled=False),
            **verdict_counts,
        }

    async def _run_agent(
        self,
        ctx: NodeContext,
        svc,
        queue: list,
        settings,
        stats: cascade.TierStats,
    ) -> bool:
        """T3 族审 + 传播；cascade 关闭则串行全价 agent。返回 True=取消。"""
        from app.contexts.finding.models import AlertGroup

        if not getattr(settings, "triage_cascade_enabled", False):
            return await self._adjudicate_all_serial(ctx, queue, settings, stats)

        if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
            return True

        rule_of, _ = await cascade.rule_by_rep(
            ctx.db_session, [g.representative_finding_id for g in queue]
        )
        if getattr(settings, "triage_family_enabled", True):
            families = [f for f in cascade.group_families(queue, rule_of) if f.members]
        else:
            families = [
                cascade.Family(key=g.group_key, members=[g]) for g in queue
            ]
        stats.families = len(families)
        emit_phase(
            ctx,
            f"族级审议：升级 {len(queue)} 组 / {len(families)} 族",
            phase=self.node_key,
        )
        if await self._adjudicate_representatives(ctx, families, settings, stats):
            return True

        await ctx.db_session.flush()
        if queue:
            await ctx.db_session.execute(
                select(AlertGroup)
                .where(AlertGroup.id.in_([g.id for g in queue]))
                .execution_options(populate_existing=True)
            )

        factor = await cascade.calibrated_propagate_factor(
            ctx.db_session,
            default_factor=float(
                getattr(settings, "triage_propagate_confidence_factor", 0.85)
            ),
            min_verified=int(getattr(settings, "triage_feedback_min_verified", 10)),
            project_id=ctx.project_id,
        )

        for family in families:
            rep = await ctx.db_session.get(AlertGroup, family.representative.id)
            if rep is None or rep.status != "adjudicated":
                continue
            propagated, review = await cascade.propagate_family_verdicts(
                svc, family=family, rep=rep, settings=settings, factor=factor,
            )
            stats.propagated += propagated
            stats.propagated_review += review
        await ctx.db_session.commit()
        return False

    async def _adjudicate_all_serial(
        self, ctx: NodeContext, queue: list, settings, stats: cascade.TierStats,
    ) -> bool:
        # 瞬时 LLM 错误连败计数：升级 raise 直接上抛（本路径无逐组兜底）
        transient_state: dict[str, Any] = {"streak": 0, "escalated": False}
        for i, group in enumerate(queue):
            if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
                emit_phase(ctx, "任务已取消，中止二审", phase=self.node_key)
                return True
            from app.contexts.agent.usage_ledger import budget_state

            exhausted, spent, budget = await budget_state(
                ctx.db_session, ctx.task_id
            )
            if exhausted:
                stats.budget_exhausted = True
                emit_phase(
                    ctx,
                    f"token 预算耗尽（{spent}/{budget}），停止剩余 agent 审议，未审组转人工",
                    phase=self.node_key,
                )
                return False
            label = f"{group.cwe or '?'} {group.file_path or ''}".strip()
            emit_phase(
                ctx, f"二审 {i + 1}/{len(queue)}：{label}", phase=self.node_key
            )
            if await adjudicate_group(ctx, group, settings, transient_state):
                stats.agent += 1
                await self._emit_progress(
                    ctx,
                    stats,
                    pending=len(queue) - i - 1,
                    done=i + 1,
                    total=len(queue),
                    label=label,
                )
            await ctx.db_session.commit()
        return False

    async def _adjudicate_representatives(
        self, ctx: NodeContext, families: list, settings, stats: cascade.TierStats,
    ) -> bool:
        from app.contexts.agent.llm_errors import is_fatal_llm_error
        from app.contexts.finding.models import AlertGroup
        from app.core.agent_runner import AgentRunnerError

        factory = getattr(ctx, "session_factory", None)
        concurrency = max(1, int(getattr(settings, "triage_concurrency", 4) or 1))
        if factory is None:
            return await self._adjudicate_all_serial(
                ctx, [f.representative for f in families], settings, stats,
            )

        sem = asyncio.Semaphore(concurrency)
        done = 0
        total = len(families)
        llm_stop = asyncio.Event()
        llm_fatal: list[str] = []
        # 瞬时 LLM 错误连败计数（跨组共享）：连续多组降级视为网关故障升级中止
        transient_state: dict[str, Any] = {"streak": 0, "escalated": False}
        emit_phase(
            ctx,
            f"族级代表审议启动：{total} 族 · 并发 {concurrency}"
            f"（每族只审 1 个代表，其余成员传播判决）",
            phase=self.node_key,
        )

        async def _one(family) -> str:
            nonlocal done
            async with sem:
                if llm_stop.is_set():
                    return "llm_skip"
                rep_gid = family.representative.id
                label = (
                    f"{family.representative.cwe or '?'} "
                    f"{family.representative.file_path or ''}"
                ).strip()
                async with factory() as ws:
                    if await task_run_cancelled(ws, ctx.task_id, ctx.run_id):
                        return "cancelled"
                    from app.contexts.agent.usage_ledger import budget_state

                    exhausted, spent, budget = await budget_state(ws, ctx.task_id)
                    if exhausted:
                        return "budget"
                    if llm_stop.is_set():
                        return "llm_skip"
                    group = await ws.get(AlertGroup, rep_gid)
                    if group is None:
                        return "missing"
                    # 开始只记流水；左侧进度条用完成后的 done/total，避免并发下
                    # 「刚启动的第 N 族」与右侧仍在输出的其它族 Agent 流对不上。
                    emit_phase(
                        ctx,
                        f"开始审议：{label}（族内 {len(family.members)} 组）",
                        phase=self.node_key,
                    )
                    try:
                        ok = await adjudicate_group(
                            replace(ctx, db_session=ws), group, settings,
                            transient_state,
                        )
                    except asyncio.CancelledError:
                        raise
                    except AgentRunnerError as e:
                        if is_fatal_llm_error(str(e)) or transient_state.get(
                            "escalated"
                        ):
                            llm_fatal.append(str(e))
                            llm_stop.set()
                            emit_phase(
                                ctx, f"LLM 调用失败，中止二审：{label}",
                                phase=self.node_key,
                            )
                            return "llm_fatal"
                        emit_phase(
                            ctx, f"二审异常转人工：{label}", phase=self.node_key
                        )
                        from app.contexts.finding.service import FindingService

                        await FindingService(ws).mark_needs_review(group)
                        ok = False
                    except Exception as e:  # noqa: BLE001
                        import logging

                        if is_fatal_llm_error(str(e)) or transient_state.get(
                            "escalated"
                        ):
                            llm_fatal.append(str(e))
                            llm_stop.set()
                            emit_phase(
                                ctx, f"LLM 调用失败，中止二审：{label}",
                                phase=self.node_key,
                            )
                            return "llm_fatal"
                        logging.getLogger(__name__).warning(
                            "代表审议异常转人工 %s: %s", label, e, exc_info=True,
                        )
                        emit_phase(
                            ctx, f"二审异常转人工：{label}", phase=self.node_key
                        )
                        from app.contexts.finding.service import FindingService

                        await FindingService(ws).mark_needs_review(group)
                        ok = False
                    await ws.commit()
                if ok:
                    stats.agent += 1
                done += 1
                family_note = f"（族内 {len(family.members)} 组）"
                emit_phase(
                    ctx,
                    f"二审 {done}/{total}：{label}{family_note}",
                    phase=self.node_key,
                )
                await self._emit_progress(
                    ctx,
                    stats,
                    pending=total - done,
                    done=done,
                    total=total,
                    label=label,
                    family_size=len(family.members),
                )
                return "ok"

        results = await asyncio.gather(*[_one(f) for f in families])
        if any(r == "cancelled" for r in results):
            emit_phase(ctx, "任务已取消，中止二审", phase=self.node_key)
            return True
        if any(r == "llm_fatal" for r in results):
            raise AgentRunnerError(
                llm_fatal[0] if llm_fatal else "AI 节点 triage LLM 调用失败"
            )
        if any(r == "budget" for r in results):
            from app.contexts.agent.usage_ledger import budget_state

            _ex, spent, budget = await budget_state(ctx.db_session, ctx.task_id)
            stats.budget_exhausted = _ex
            emit_phase(
                ctx,
                f"token 预算耗尽（{spent}/{budget}），停止剩余 agent 审议，未审组转人工",
                phase=self.node_key,
            )
            if ctx.on_event:
                ctx.on_event({
                    "type": "triage.progress",
                    "node_key": self.node_key,
                    "reason": "budget",
                    "spent": spent,
                    "budget": budget,
                    "adjudicated": stats.agent,
                    "pending": max(0, total - done),
                    "done": done,
                    "total": total,
                    "message": (
                        f"token 预算耗尽（{spent}/{budget}），"
                        f"已审 {done}/{total}"
                    ),
                })
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
        self,
        stats: cascade.TierStats,
        skipped_llm: int,
        leftover: int,
        *,
        cancelled: bool,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {
            "adjudicated_count": stats.agent,
            "skipped_llm_count": skipped_llm,
            "skipped_unaudited_count": leftover,
            "carried_count": stats.carried,
            "rule_count": stats.rule,
            "fast_model_count": stats.fast,
            "propagated_count": stats.propagated,
            "propagated_review_count": stats.propagated_review,
            "family_count": stats.families,
            "escalated_count": stats.escalated,
            "budget_exhausted": stats.budget_exhausted,
        }
        if cancelled:
            out["status"] = "cancelled"
        return out

    async def _emit_progress(
        self,
        ctx: NodeContext,
        stats: cascade.TierStats,
        *,
        pending: int,
        done: int | None = None,
        total: int | None = None,
        label: str = "",
        family_size: int | None = None,
    ) -> None:
        if not ctx.on_event:
            return
        finished = done if done is not None else stats.agent
        payload: dict[str, Any] = {
            "type": "triage.progress",
            "node_key": self.node_key,
            "adjudicated": stats.agent,
            "pending": pending,
            "done": finished,
            "tiers": {
                "carried": stats.carried,
                "rule": stats.rule,
                "fast_model": stats.fast,
                "agent": stats.agent,
                "propagated": stats.propagated,
            },
        }
        if total is not None:
            payload["total"] = total
        if label:
            payload["label"] = label
        if family_size is not None:
            payload["family_size"] = family_size
        if total is not None and label:
            note = f"（族内 {family_size} 组）" if family_size is not None else ""
            payload["message"] = f"二审 {finished}/{total}：{label}{note}"
        elif total is not None:
            payload["message"] = f"二审 {finished}/{total}"
        else:
            payload["message"] = f"已审 {stats.agent}，待审 {pending}"
        ctx.on_event(payload)
