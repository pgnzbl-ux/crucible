"""finding context repository — 过滤查询。"""
from __future__ import annotations

from sqlalchemy import String, and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.finding.models import AlertGroup, RawFinding, ReviewAction


class FindingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_groups(
        self, *, task_id: str | None = None, status: str | None = None,
        cwe: str | None = None, ai_verdict: str | None = None,
        engine: str | None = None, clue_grade: str | None = None,
        scope: str | None = None,
        limit: int = 50, offset: int = 0, owner_task_ids: list[str] | None = None,
    ) -> tuple[int, list[AlertGroup]]:
        stmt = select(AlertGroup)
        if owner_task_ids is not None:
            stmt = stmt.where(AlertGroup.task_id.in_(owner_task_ids))
        if task_id:
            stmt = stmt.where(AlertGroup.task_id == task_id)
        if status:
            stmt = stmt.where(AlertGroup.status == status)
        if cwe:
            stmt = stmt.where(AlertGroup.cwe == cwe)
        if ai_verdict:
            stmt = stmt.where(AlertGroup.ai_verdict == ai_verdict)
        if clue_grade:
            stmt = stmt.where(AlertGroup.clue_grade == clue_grade)
        if engine:
            stmt = stmt.where(self.engine_member_clause(engine))
        if scope and scope != "all":
            stmt = stmt.where(self.queue_scope_clause(scope))
        total = (await self.session.execute(
            select(func.count()).select_from(stmt.subquery())
        )).scalar_one()
        rows = await self.session.execute(
            stmt.order_by(AlertGroup.created_at.desc()).limit(limit).offset(offset)
        )
        return int(total), list(rows.scalars().all())

    @staticmethod
    def engine_member_clause(engine: str):
        """engine_set 是 json 列（没有 jsonb 的 @> 包含运算符），转文本后按
        JSON 字符串元素精确匹配：带引号保证 "osv" 不会命中 "osv-scanner"，
        LIKE 通配符转义保证筛选值不会被当成模式。"""
        escaped = engine.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return AlertGroup.engine_set.cast(String).like(f'%"{escaped}"%', escape="\\")

    @staticmethod
    def queue_scope_clause(scope: str):
        """工作队列四分法：重点、需复核、初筛中、已降噪，互斥且覆盖全部。"""
        resolution = func.coalesce(AlertGroup.resolution, "")
        verdict = func.coalesce(AlertGroup.ai_verdict, "")
        grade = func.coalesce(AlertGroup.clue_grade, "")
        priority = func.coalesce(AlertGroup.priority, "")
        noise = or_(
            resolution.in_(("false_positive", "ignored")),
            verdict == "fp",
            and_(AlertGroup.status == "needs_review", grade == "F"),
            and_(AlertGroup.status == "needs_review", priority == "low", verdict == ""),
        )
        processing = and_(not_(noise), AlertGroup.status.in_(("new", "clustered")))
        review = and_(
            not_(noise), not_(processing),
            or_(verdict == "need_more_context", AlertGroup.status == "needs_review"),
        )
        clauses = {
            "noise": noise,
            "processing": processing,
            "review": review,
            "focus": not_(or_(noise, processing, review)),
        }
        return clauses[scope]

    async def group_queue_stats(self, owner_task_ids: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for scope in ("focus", "review", "processing", "noise"):
            count = await self.session.scalar(
                select(func.count(AlertGroup.id)).where(
                    AlertGroup.task_id.in_(owner_task_ids),
                    self.queue_scope_clause(scope),
                )
            )
            result[scope] = int(count or 0)
        return result

    async def group_stats(
        self, owner_task_ids: list[str],
    ) -> tuple[dict[str, int], dict[str, int]]:
        status_rows = await self.session.execute(
            select(AlertGroup.status, func.count(AlertGroup.id))
            .where(AlertGroup.task_id.in_(owner_task_ids))
            .group_by(AlertGroup.status)
        )
        resolution_rows = await self.session.execute(
            select(AlertGroup.resolution, func.count(AlertGroup.id))
            .where(
                AlertGroup.task_id.in_(owner_task_ids),
                AlertGroup.resolution.is_not(None),
            )
            .group_by(AlertGroup.resolution)
        )
        return (
            {str(status): int(count) for status, count in status_rows.all()},
            {str(resolution): int(count) for resolution, count in resolution_rows.all()},
        )

    async def list_group_members(self, group: AlertGroup) -> list[RawFinding]:
        rows = await self.session.execute(
            select(RawFinding).where(
                RawFinding.task_id == group.task_id,
                RawFinding.file_path == group.file_path,
            ).order_by(RawFinding.created_at)
        )
        return list(rows.scalars().all())

    async def get_representative(self, group: AlertGroup) -> RawFinding | None:
        return await self.session.get(RawFinding, group.representative_finding_id)

    async def list_reviews(self, group_id: str) -> list[ReviewAction]:
        rows = await self.session.execute(
            select(ReviewAction).where(ReviewAction.alert_group_id == group_id)
            .order_by(ReviewAction.created_at.desc())
        )
        return list(rows.scalars().all())
