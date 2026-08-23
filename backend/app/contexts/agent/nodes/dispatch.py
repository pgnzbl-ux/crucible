"""dispatch 节点 — 合格线索入 Redis 终认队列(discovery-spec §6.4 / v1.2)。

硬规则：禁止自动创建 task_type=verify；禁止回填 vulnerability_description；
禁止把多条线索拼进同一次 audit prompt；无合格线索 has_lead=False(节点仍 completed)。
"""
from __future__ import annotations

from typing import Any

from app.contexts.finding.hypothesis import RUBRIC_COVERED_CWES, build_lead_description

from .base import NodeContext, emit_phase

# 入队排序键(§6.4)：priority → 评分表覆盖 → severity → member_count
# → ai_confidence → group_id 字典序
_PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2, None: 3}
_SEVERITY_RANK = {"error": 0, "warning": 1, None: 2, "": 2, "note": 3, "info": 3}

def _sort_candidates(candidates: list, rep_severity: dict[str, str | None]) -> list:
    return sorted(
        candidates,
        key=lambda g: (
            _PRIORITY_RANK.get(g.priority, 3),
            0 if (g.cwe or "") in RUBRIC_COVERED_CWES else 1,
            _SEVERITY_RANK.get(rep_severity.get(g.id, ""), 2),
            -(g.member_count or 1),
            -float(g.ai_confidence or 0.0),
            g.id,
        ),
    )


async def _get_or_create_lead_run(
    session, *, task_id: str, run_id: str, alert_group_id: str,
    queue_position: int, description: str,
):
    """幂等建 LeadRun：撞 uq_lead_runs_run_group(Celery 重投/双 worker)时复用已有行。"""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.contexts.finding.models import LeadRun

    row = (await session.execute(
        select(LeadRun).where(
            LeadRun.run_id == run_id, LeadRun.alert_group_id == alert_group_id,
        )
    )).scalar_one_or_none()
    if row is not None:
        return row
    row = LeadRun(
        task_id=task_id, run_id=run_id, alert_group_id=alert_group_id,
        queue_position=queue_position, lead_description=description,
        status="queued",
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
        await session.refresh(row)
        return row
    except IntegrityError:
        found = (await session.execute(
            select(LeadRun).where(
                LeadRun.run_id == run_id, LeadRun.alert_group_id == alert_group_id,
            )
        )).scalar_one_or_none()
        if found is None:
            raise
        return found


async def _reconcile_lead_runs(ctx, *, exclude_ids: set[str]) -> list[tuple]:
    """重投补偿：queued 状态 LeadRun 绑回当前 run；不在 Redis 队列的补入队。

    覆盖两个崩溃窗口：commit 后入队前崩溃（队列空）；入队后进程崩溃（Redis 残留
    旧 run 条目）。终态(completed/failed/skipped) LeadRun 不动。
    """
    from sqlalchemy import select

    from app.contexts.agent.lead_queue import queued_lead_ids
    from app.contexts.finding.models import LeadRun

    rows = (await ctx.db_session.execute(
        select(LeadRun).where(
            LeadRun.task_id == ctx.task_id, LeadRun.status == "queued",
        )
    )).scalars().all()
    if not rows:
        return []
    redis_ids = await queued_lead_ids(ctx.task_id)
    recovered: list[tuple] = []
    for lr in rows:
        if lr.run_id != ctx.run_id:
            # 组已 dispatched 不会进本轮候选，(run_id, alert_group_id) 无撞码
            lr.run_id = ctx.run_id
        if lr.id not in redis_ids and lr.id not in exclude_ids:
            recovered.append((lr, {
                "lead_run_id": lr.id,
                "group_id": lr.alert_group_id,
                "run_id": ctx.run_id,
            }))
    return recovered


class DispatchNode:
    node_key = "dispatch"

    @property
    def is_ai(self) -> bool:
        return False

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "dispatch",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from sqlalchemy import func, select

        from app.contexts.agent.lead_queue import enqueue_leads
        from app.contexts.finding.models import LeadRun
        from app.contexts.finding.service import FindingService
        from app.contexts.task.models import Task
        from app.contexts.task.repository import TaskRepository
        from app.contexts.task.service import TaskService
        from app.core.config import get_settings

        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp
        settings = get_settings()
        svc = FindingService(ctx.db_session)
        await ctx.db_session.get(Task, ctx.task_id)

        groups = await svc.list_groups(ctx.task_id)
        is_web = getattr(inp.profile, "is_web", None) is True
        high = float(settings.triage_high_confidence)

        candidates: list = []
        review_count = archived_count = unaudited = 0

        for g in groups:
            verdict = g.ai_verdict
            if verdict == "bypass":
                continue
            if g.status == "dispatched":
                # 已入终认队列/处理中：不重复建线索；其 Redis 条目丢失由
                # _reconcile_lead_runs 补偿恢复
                continue
            if verdict is None:
                unaudited += 1
                continue
            if verdict == "need_more_context":
                await svc.mark_needs_review(g)
                review_count += 1
                continue
            if verdict == "fp":
                archived_count += 1
                continue
            conf = float(g.ai_confidence or 0.0)
            grade = (g.clue_grade or "B")
            if conf >= high and grade == "A" and is_web:
                candidates.append(g)
            else:
                await svc.mark_needs_review(g)
                review_count += 1

        ordered: list = []
        if candidates:
            rep_severity: dict[str, str | None] = {}
            for g in candidates:
                rep = await svc.representative_of(g)
                rep_severity[g.id] = (rep.severity if rep else None) or ""
            ordered = _sort_candidates(candidates, rep_severity)

        queued_ids: list[str] = []
        queue_items: list[dict[str, str]] = []
        lead_description = None
        lead_group_id = None

        for pos, g in enumerate(ordered):
            rep = await svc.representative_of(g)
            adj = await svc.latest_adjudication(g.id)
            desc = build_lead_description(group=g, representative=rep, adjudication=adj)
            lead_run = await _get_or_create_lead_run(
                ctx.db_session,
                task_id=ctx.task_id, run_id=ctx.run_id,
                alert_group_id=g.id, queue_position=pos, description=desc,
            )
            await svc.mark_dispatched(g)
            queued_ids.append(g.id)
            queue_items.append({
                "lead_run_id": lead_run.id,
                "group_id": g.id,
                "run_id": ctx.run_id,
            })
            if pos == 0:
                lead_group_id = g.id
                lead_description = desc
                await TaskService(TaskRepository(ctx.db_session)).set_source_alert_group(
                    ctx.task_id, g.id,
                )

        # 重投补偿：上次 commit 后、入队前崩溃遗留的 queued LeadRun 绑回当前 run
        # 并补入队；入队后崩溃残留的旧 run 条目只改绑不重复入队。不静默丢线索。
        recovered = await _reconcile_lead_runs(
            ctx, exclude_ids={i["lead_run_id"] for i in queue_items},
        )
        for lr, item in recovered:
            queue_items.append(item)
        if recovered and lead_group_id is None:
            lead_group_id = recovered[0][1]["group_id"]
            lead_description = recovered[0][0].lead_description

        await ctx.db_session.commit()

        # 活动线索以 DB 为准（重投时本轮可能零新增但队列已有线索）
        active_leads = (await ctx.db_session.execute(
            select(func.count()).select_from(LeadRun).where(
                LeadRun.task_id == ctx.task_id,
                LeadRun.status.in_(("queued", "running")),
            )
        )).scalar_one()

        if queue_items:
            pushed = await enqueue_leads(ctx.task_id, queue_items)
            if pushed != len(queue_items):
                # Redis 半失败：节点 fail，重投后由 _reconcile_lead_runs 补齐
                raise RuntimeError(
                    f"终认队列入队不完整：期望 {len(queue_items)} 实推 {pushed}"
                )
            rep = ordered[0] if ordered else None
            emit_phase(
                ctx,
                f"入队 {len(queue_items)} 条终认线索（代表：{(rep.cwe if rep else 'CWE-?')} {(rep.file_path if rep else '')}）",
                phase=self.node_key,
            )
        elif active_leads:
            emit_phase(ctx, f"线索已在终认队列（{active_leads} 条），无需重复入队", phase=self.node_key)
        else:
            emit_phase(ctx, "无合格主线索，终认将跳过", phase=self.node_key)

        return {
            "has_lead": bool(queued_ids) or active_leads > 0,
            "queued_count": int(active_leads),
            "queued_group_ids": queued_ids,
            "lead_group_id": lead_group_id,
            "lead_description": lead_description,
            "review_count": review_count,
            "archived_count": archived_count,
            "skipped_unaudited_count": unaudited,
        }
