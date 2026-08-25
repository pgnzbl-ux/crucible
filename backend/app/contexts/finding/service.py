"""finding context service — AlertGroup 状态机唯一入口(discovery-spec §5.3)。

repository 不暴露 status 直写；所有转移经这里的显式方法。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.finding.models import Adjudication, AlertGroup, LeadRun, RawFinding, ReviewAction


def numeric_usage(usage: dict | None) -> dict[str, int]:
    """agent 侧 SDK usage 原样回传(含 server_tool_use 等嵌套 dict/str 字段)，
    只保留数值项 —— 列契约是 {token 计数}，其余进 response_text 审计链。"""
    return {
        str(k): int(v)
        for k, v in (usage or {}).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def _sanitize_adjudication(a: Adjudication) -> Adjudication:
    """agent 自由输出收敛到判决列契约(why/need 全 str、evidence 全 dict、
    usage 全数值)，防止单条脏行打挂复核台详情读取。"""
    a.why = [str(w) for w in (a.why or [])]
    a.need = [str(n) for n in (a.need or [])]
    a.evidence = [e if isinstance(e, dict) else {"detail": str(e)} for e in (a.evidence or [])]
    a.usage = numeric_usage(a.usage)
    return a


class FindingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 查询 ──

    async def list_findings(self, task_id: str) -> list[RawFinding]:
        result = await self.session.execute(
            select(RawFinding).where(RawFinding.task_id == task_id)
        )
        return list(result.scalars().all())

    async def representative_of(self, group: AlertGroup) -> RawFinding | None:
        """组的代表 finding（dispatch/triage 组装线索用；跨 Context 唯一入口）。"""
        if not group.representative_finding_id:
            return None
        return await self.session.get(RawFinding, group.representative_finding_id)

    async def latest_adjudication(self, group_id: str) -> Adjudication | None:
        """组最近一次二审记录（按 attempt 降序取首条）。"""
        result = await self.session.execute(
            select(Adjudication)
            .where(Adjudication.alert_group_id == group_id)
            .order_by(Adjudication.attempt.desc())
        )
        rows = result.scalars().all()
        return rows[0] if rows else None

    async def list_groups(self, task_id: str, *, status: str | None = None) -> list[AlertGroup]:
        stmt = select(AlertGroup).where(AlertGroup.task_id == task_id)
        if status:
            stmt = stmt.where(AlertGroup.status == status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_group(self, group_id: str) -> AlertGroup | None:
        return await self.session.get(AlertGroup, group_id)

    async def list_adjudications(self, group_id: str) -> list[Adjudication]:
        result = await self.session.execute(
            select(Adjudication).where(Adjudication.alert_group_id == group_id)
            .order_by(Adjudication.attempt)
        )
        return list(result.scalars().all())

    # ── cluster 侧：分组落库(幂等合并) ──

    async def upsert_groups(
        self, *, task_id: str, groups: list[dict[str, Any]],
        finding_by_id: dict[str, RawFinding],
    ) -> int:
        """按 (task_id, group_key) upsert：已存在则合并成员/引擎集，不重建。"""
        if not groups:
            return 0
        keys = [g["group_key"] for g in groups]
        result = await self.session.execute(
            select(AlertGroup).where(
                AlertGroup.task_id == task_id, AlertGroup.group_key.in_(keys)
            )
        )
        existing = {g.group_key: g for g in result.scalars().all()}
        created = 0
        for g in groups:
            rep_id = g.get("representative_finding_id")
            if not rep_id:
                continue  # 无代表成员的组不落库(代表必须指向真实 RawFinding)
            row = existing.get(g["group_key"])
            if row is None:
                row = AlertGroup(
                    task_id=task_id,
                    group_key=g["group_key"],
                    cwe=g.get("cwe"),
                    file_path=g.get("file_path") or "",
                    function_symbol=g.get("function_symbol"),
                    line_span=g.get("line_span"),
                    member_count=g.get("member_count") or 1,
                    representative_finding_id=rep_id,
                    engine_set=g.get("engine_set") or [],
                    status="clustered",
                    clue_grade=g.get("clue_grade"),
                    priority=g.get("priority"),
                )
                try:
                    async with self.session.begin_nested():
                        self.session.add(row)
                        await self.session.flush()
                    existing[g["group_key"]] = row
                    created += 1
                    continue
                except IntegrityError:
                    # 并发(重投双跑)撞 uq_alert_groups_task_key：复用已有行合并，
                    # 与 _get_or_create_node_run 同模式
                    found = (await self.session.execute(
                        select(AlertGroup).where(
                            AlertGroup.task_id == task_id,
                            AlertGroup.group_key == g["group_key"],
                        )
                    )).scalar_one_or_none()
                    if found is None:
                        raise
                    row = found
                    existing[g["group_key"]] = row
            row.member_count = max(row.member_count, g.get("member_count") or 1)
            engines = set(row.engine_set or []) | set(g.get("engine_set") or [])
            row.engine_set = sorted(engines)
            row.priority = g.get("priority") or row.priority
            row.clue_grade = g.get("clue_grade") or row.clue_grade
        await self.session.flush()
        return created

    async def mark_bypass_groups(self, task_id: str) -> int:
        """osv 组：clustered → adjudicated(bypass 直报，跳过 triage)。"""
        groups = await self.list_groups(task_id)
        n = 0
        for g in groups:
            if "osv" in (g.engine_set or []) and g.status == "clustered":
                g.status = "adjudicated"
                g.ai_verdict = "bypass"
                g.priority = g.priority or "medium"
                n += 1
        await self.session.flush()
        return n

    # ── triage 侧：判决落库 ──

    async def record_adjudication(
        self, *, group: AlertGroup, adjudication: Adjudication,
    ) -> None:
        self.session.add(_sanitize_adjudication(adjudication))
        group.status = "adjudicated"
        group.ai_verdict = adjudication.verdict
        group.ai_confidence = adjudication.confidence
        await self.session.flush()

    async def mark_unaudited_for_review(self, task_id: str) -> int:
        """未审组保持 clustered，只计数；禁止记 fp、禁止转人工主队列。"""
        groups = await self.list_groups(task_id, status="clustered")
        return len(groups)

    # ── dispatch / 复核侧 ──

    async def mark_dispatched(self, group: AlertGroup) -> None:
        group.status = "dispatched"
        await self.session.flush()

    async def mark_needs_review(self, group: AlertGroup, *, priority: str | None = None) -> None:
        group.status = "needs_review"
        if priority:
            group.priority = priority
        await self.session.flush()

    async def mark_resolved(self, group: AlertGroup, resolution: str) -> None:
        """终态：confirmed | partial | code_reachable | false_positive | ignored。"""
        if resolution not in (
            "confirmed", "partial", "code_reachable", "false_positive", "ignored",
        ):
            raise ValueError(f"非法 resolution: {resolution}")
        group.status = "resolved"
        group.resolution = "confirmed" if resolution == "partial" else resolution
        await self.session.flush()

    async def discard_false_positive(self, group: AlertGroup) -> None:
        """明确误报：级联删组与判决，保留 RawFinding。线索台无对象。"""
        gid = group.id
        await self.session.execute(
            delete(Adjudication).where(Adjudication.alert_group_id == gid)
        )
        await self.session.execute(
            delete(ReviewAction).where(ReviewAction.alert_group_id == gid)
        )
        await self.session.execute(delete(LeadRun).where(LeadRun.alert_group_id == gid))
        await self.session.execute(delete(AlertGroup).where(AlertGroup.id == gid))
        await self.session.flush()

    async def discard_task_false_positives(self, task_id: str) -> int:
        """漏斗计数之后丢掉误报组，线索台无对象。"""
        n = 0
        for g in await self.list_groups(task_id):
            if g.ai_verdict == "fp" and g.status not in ("dispatched", "resolved"):
                await self.discard_false_positive(g)
                n += 1
        return n

    async def revive(self, group: AlertGroup) -> None:
        """遗留：历史误报组若仍在库，可退回 needs_review。新误报无对象。"""
        group.status = "needs_review"
        await self.session.flush()

    # ── 判决回流(§4.4)：六档 verdict → AlertGroup 终态；幂等 ──

    _CONFIRMED = ("confirmed", "partial")

    async def reconcile_from_task(self, task) -> AlertGroup | None:
        """按 Task 终态回写其溯源组。同任务终认与复核台派生走同一函数。

        - confirmed/partial → resolved(confirmed)
        - code_reachable → resolved(code_reachable)
        - false_positive → 丢弃组（漏斗计数，不展示）
        - code_smell/not_reproduced → 退回 needs_review（线索台默认不展示）
        - 任务 needs_review(status) → 组退回 needs_review
        幂等：组已 resolved 且映射未变则 no-op。丢事件兜底走 reconcile_stale_groups。
        """
        group_id = getattr(task, "source_alert_group_id", None)
        if not group_id:
            return None
        group = await self.get_group(group_id)
        if group is None:
            return None
        if task.status == "needs_review":
            if group.status == "dispatched":
                group.status = "needs_review"
                await self.session.flush()
            return group
        verdict = getattr(task, "verdict", None)
        if verdict in self._CONFIRMED:
            if not (group.status == "resolved" and group.resolution == "confirmed"):
                await self.mark_resolved(group, "confirmed")
        elif verdict == "false_positive":
            await self.discard_false_positive(group)
            return None
        elif verdict == "code_reachable":
            if not (group.status == "resolved" and group.resolution == "code_reachable"):
                await self.mark_resolved(group, "code_reachable")
        elif verdict in ("code_smell", "not_reproduced"):
            if group.status == "dispatched":
                group.status = "needs_review"
                await self.session.flush()
        return group

    async def reconcile_stale_groups(self, *, owner_id: str | None = None) -> int:
        """低频 sweeper(§4.4 丢事件兜底)：dispatched 组按 Task 最新 verdict 补写。"""
        from sqlalchemy import select

        from app.contexts.task.models import Task

        result = await self.session.execute(
            select(AlertGroup).where(AlertGroup.status == "dispatched")
        )
        stale = [g for g in result.scalars().all()]
        fixed = 0
        for group in stale:
            task_rows = await self.session.execute(
                select(Task).where(Task.source_alert_group_id == group.id)
            )
            task = task_rows.scalars().first()
            if task is None:
                continue
            before = group.status
            await self.reconcile_from_task(task)
            if group.status != before:
                fixed += 1
        if fixed:
            await self.session.flush()
        return fixed

    async def record_review_action(
        self, *, group_id: str, user_id: str, action: str,
        reason_tags: list[str] | None = None, reason_text: str | None = None,
    ) -> ReviewAction:
        action_row = ReviewAction(
            alert_group_id=group_id, user_id=user_id, action=action,
            reason_tags=reason_tags or [], reason_text=reason_text,
        )
        self.session.add(action_row)
        await self.session.flush()
        return action_row

    async def delete_groups(
        self, group_ids: list[str], *, owner_id: str,
    ) -> tuple[list[str], list[dict[str, str]]]:
        """物理删除告警组（级联判决/复核/LeadRun；保留 RawFinding 与验证 Task）。

        终认进行中（LeadRun queued/running，或溯源验证 Task 仍 pending/queued/running）
        的组跳过，避免孤儿写回。跨 Context 只清逻辑指针 source_alert_group_id。
        """
        from app.contexts.task.models import Task

        unique_ids = list(dict.fromkeys(group_ids))
        if not unique_ids:
            return [], []

        owned_rows = await self.session.execute(
            select(AlertGroup.id)
            .join(Task, Task.id == AlertGroup.task_id)
            .where(AlertGroup.id.in_(unique_ids), Task.owner_id == owner_id)
        )
        owned = list(owned_rows.scalars().all())
        owned_set = set(owned)
        skipped: list[dict[str, str]] = [
            {"id": gid, "reason": "not_found"}
            for gid in unique_ids
            if gid not in owned_set
        ]
        if not owned:
            return [], skipped

        active_leads = await self.session.execute(
            select(LeadRun.alert_group_id).where(
                LeadRun.alert_group_id.in_(owned),
                LeadRun.status.in_(("queued", "running")),
            ).distinct()
        )
        blocked = set(active_leads.scalars().all())

        active_verify = await self.session.execute(
            select(Task.source_alert_group_id).where(
                Task.source_alert_group_id.in_(owned),
                Task.status.in_(("pending", "queued", "running")),
            ).distinct()
        )
        blocked.update(g for g in active_verify.scalars().all() if g)

        deletable = [gid for gid in owned if gid not in blocked]
        for gid in owned:
            if gid in blocked:
                skipped.append({"id": gid, "reason": "in_progress"})

        if not deletable:
            return [], skipped

        await self.session.execute(
            update(Task)
            .where(Task.source_alert_group_id.in_(deletable))
            .values(source_alert_group_id=None)
        )
        await self.session.execute(
            delete(Adjudication).where(Adjudication.alert_group_id.in_(deletable))
        )
        await self.session.execute(
            delete(ReviewAction).where(ReviewAction.alert_group_id.in_(deletable))
        )
        await self.session.execute(
            delete(LeadRun).where(LeadRun.alert_group_id.in_(deletable))
        )
        await self.session.execute(
            delete(AlertGroup).where(AlertGroup.id.in_(deletable))
        )
        await self.session.flush()
        return deletable, skipped

    async def count_groups(self, task_id: str) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(AlertGroup).where(AlertGroup.task_id == task_id)
        )
        return int(result.scalar_one())
