"""流式派单：达终认门槛的组判完即入队，后台并发排空。

screen / triage 共用；phase 文案跟调用方 node_key。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from sqlalchemy import select

from ..base import emit_phase

_ENV_READY_ABSENT_GRACE = 0.2
_ENV_READY_WAIT_SECONDS = 1800.0


async def _resolve_env_ready(session_factory, run_id: str, fallback: Any) -> Any:
    """drain 时读库内 env_ready 终态，避免与 screen 并行时用 spawn 快照。"""
    from app.contexts.task.models import NodeRun

    started = time.monotonic()
    saw_row = False
    while True:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(NodeRun).where(
                        NodeRun.run_id == run_id, NodeRun.node_key == "env_ready",
                    )
                )
            ).scalar_one_or_none()
        if row is not None:
            saw_row = True
            if row.status in ("completed", "skipped"):
                try:
                    parsed = json.loads(row.output_json or "null")
                except (ValueError, TypeError):
                    return fallback
                return parsed if parsed else fallback
            if row.status in ("failed", "cancelled"):
                return None
        elapsed = time.monotonic() - started
        if not saw_row and elapsed >= _ENV_READY_ABSENT_GRACE:
            return fallback
        if elapsed >= _ENV_READY_WAIT_SECONDS:
            return fallback
        await asyncio.sleep(0.25)


class LeadStreamer:
    """达合格门的组立即建 LeadRun 入队并后台排空。

    门槛与 dispatch 节点完全一致（discovery-spec §2.7，漏报优先）；
    poll() 以库内最新状态为准。
    """

    def __init__(self, ctx, settings, *, phase: str = "triage"):
        self.ctx = ctx
        self.settings = settings
        self.phase = phase
        # 兼容旧参数；qualify 已不再用置信硬门槛
        self.high = float(getattr(settings, "triage_high_confidence", 0.8))
        self.drain_task: asyncio.Task | None = None
        self.enqueued = 0

    async def _qualified(self, groups: list) -> list:
        pending = [g for g in groups if g.status == "adjudicated"]
        if not pending:
            return []
        from app.contexts.finding.qualify import is_qualified_lead
        from app.contexts.finding.service import FindingService

        svc = FindingService(self.ctx.db_session)
        reps, adjs = await svc.reps_and_adjudications(pending)
        return [
            g for g in pending
            if is_qualified_lead(
                g,
                representative=reps.get(g.id),
                adjudication=adjs.get(g.id),
                high_confidence=self.high,
            )
        ]

    async def poll(self) -> int:
        """库内全量扫一遍达门槛未派发的组建 LeadRun 入队（幂等）。"""
        from app.contexts.finding.models import AlertGroup

        rows = (await self.ctx.db_session.execute(
            select(AlertGroup).where(
                AlertGroup.task_id == self.ctx.task_id,
                AlertGroup.status == "adjudicated",
                AlertGroup.ai_verdict == "tp",
            ).order_by(AlertGroup.ai_confidence.desc()).execution_options(
                populate_existing=True,
            )
        )).scalars().all()
        qualified = await self._qualified(rows)
        return await self._enqueue(qualified)

    async def offer(self, groups: list) -> int:
        """对刚判完的组（内存态已刷新）做入队判断，避免全量重查。"""
        qualified = await self._qualified(groups)
        return await self._enqueue(qualified)

    async def _enqueue(self, qualified: list) -> int:
        if not qualified:
            return 0
        from app.contexts.agent.lead_queue import enqueue_leads
        from app.contexts.agent.nodes.dispatch import _get_or_create_lead_run
        from app.contexts.finding.hypothesis import build_lead_description
        from app.contexts.finding.service import FindingService

        ctx = self.ctx
        svc = FindingService(ctx.db_session)
        items: list[dict[str, str]] = []
        seen: set[str] = set()
        unique = []
        for g in qualified:
            if g.id in seen:
                continue
            seen.add(g.id)
            unique.append(g)
        reps, adjs = await svc.reps_and_adjudications(unique)
        for pos, g in enumerate(unique):
            rep = reps.get(g.id)
            adj = adjs.get(g.id)
            lead_run = await _get_or_create_lead_run(
                ctx.db_session,
                task_id=ctx.task_id, run_id=ctx.run_id,
                alert_group_id=g.id, queue_position=pos,
                description=build_lead_description(
                    group=g, representative=rep, adjudication=adj,
                ),
            )
            if lead_run.status != "queued":
                continue
            await svc.mark_dispatched(g)
            items.append({
                "lead_run_id": lead_run.id,
                "group_id": g.id,
                "run_id": ctx.run_id,
            })
        if not items:
            return 0
        from app.contexts.task.models import Task as _TaskModel
        from app.contexts.task.repository import TaskRepository
        from app.contexts.task.service import TaskService

        task_row = await ctx.db_session.get(_TaskModel, ctx.task_id)
        if task_row is not None and not getattr(task_row, "source_alert_group_id", None):
            await TaskService(TaskRepository(ctx.db_session)).set_source_alert_group(
                ctx.task_id, items[0]["group_id"],
            )
        await ctx.db_session.commit()
        try:
            pushed = await enqueue_leads(ctx.task_id, items)
        except Exception:
            from sqlalchemy import update as _update

            from app.contexts.finding.models import AlertGroup

            await ctx.db_session.execute(
                _update(AlertGroup)
                .where(AlertGroup.id.in_([i["group_id"] for i in items]))
                .values(status="adjudicated")
            )
            await ctx.db_session.commit()
            emit_phase(ctx, "终认队列入队失败，本轮流式派单回退", phase=self.phase)
            return 0
        self.enqueued += pushed or len(items)
        emit_phase(
            ctx, f"流式入队 {len(items)} 条终认线索（累计 {self.enqueued}）",
            phase=self.phase,
        )
        self._ensure_drain()
        return len(items)

    def _ensure_drain(self) -> None:
        if (
            self.drain_task is not None
            and not self.drain_task.done()
        ) or self.ctx.session_factory is None:
            return
        from app.contexts.agent.lead_worker import drain_lead_queue

        prev = self.ctx.previous_outputs or {}
        ctx = self.ctx
        run_id = ctx.run_id

        async def _drain():
            from app.contexts.settings.repository import SettingsRepository
            from app.contexts.settings.service import SettingsService

            env_ready = await _resolve_env_ready(
                ctx.session_factory, run_id, fallback=prev.get("env_ready") or None,
            )
            async with ctx.session_factory() as s:
                runtime = await SettingsService(
                    SettingsRepository(s)
                ).get_runtime_settings()
            await drain_lead_queue(
                session_factory=ctx.session_factory,
                task_id=ctx.task_id,
                host_workdir=ctx.host_workdir,
                source_path=ctx.source_path,
                runner_env=ctx.runner_env or {},
                source=prev.get("source"),
                profile=prev.get("profile") or {},
                env_ready=env_ready,
                on_event=ctx.on_event,
                lab_id=ctx.lab_id,
                concurrency=runtime.lead_verify_per_task,
            )

        self.drain_task = asyncio.create_task(_drain())

    async def join(self) -> None:
        import logging

        if self.drain_task is not None:
            try:
                await self.drain_task
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "流式排空失败(交由 dispatch 后排空重试): %s", e,
                )

    def cancel(self) -> None:
        if self.drain_task is not None and not self.drain_task.done():
            self.drain_task.cancel()

    async def cancel_and_reap(self) -> None:
        self.cancel()
        if self.drain_task is not None:
            try:
                await self.drain_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
