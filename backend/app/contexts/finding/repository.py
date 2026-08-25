"""finding context repository — 过滤查询。"""
from __future__ import annotations

from sqlalchemy import String, and_, case, func, or_, select, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import Select, bindparam
from sqlalchemy.sql.expression import ColumnElement
from sqlalchemy.types import Boolean

from app.contexts.finding.models import AlertGroup, RawFinding, ReviewAction
from app.contexts.task.models import Task

# 跨页全选一次拉取的硬顶；超出要求用户收窄筛选
MAX_CROSS_PAGE_IDS = 2000


class _JsonArrayHas(ColumnElement[bool]):
    """JSON 数组是否含某字符串元素。PG 走 jsonb `?`，SQLite 走 json_each。"""

    inherit_cache = False

    def __init__(self, column, value: str):
        super().__init__()
        self.column = column
        self.value = value
        self.type = Boolean()


@compiles(_JsonArrayHas, "postgresql")
def _json_array_has_pg(element, compiler, **kw):
    return compiler.process(
        type_coerce(element.column, JSONB).has_key(element.value), **kw,
    )


@compiles(_JsonArrayHas, "sqlite")
def _json_array_has_sqlite(element, compiler, **kw):
    col = compiler.process(element.column, **kw)
    val = compiler.process(bindparam(None, element.value, unique=True), **kw)
    return f"EXISTS (SELECT 1 FROM json_each({col}) WHERE json_each.value = {val})"


@compiles(_JsonArrayHas)
def _json_array_has_default(element, compiler, **kw):
    escaped = (
        element.value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return compiler.process(
        element.column.cast(String).like(f'%"{escaped}"%', escape="\\"), **kw,
    )


class FindingRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _filtered_groups_stmt(
        self, *, task_id: str | None = None, status: str | None = None,
        resolution: str | None = None,
        cwe: str | None = None, ai_verdict: str | None = None,
        engine: str | None = None, clue_grade: str | None = None,
        scope: str | None = None, q: str | None = None,
        owner_id: str | None = None,
        owner_task_ids: list[str] | None = None,
    ) -> Select:
        stmt = select(AlertGroup)
        need_task_join = bool((q or "").strip()) or owner_id is not None
        if need_task_join:
            stmt = stmt.join(Task, Task.id == AlertGroup.task_id)
        if owner_id is not None:
            stmt = stmt.where(Task.owner_id == owner_id)
        elif owner_task_ids is not None:
            stmt = stmt.where(AlertGroup.task_id.in_(owner_task_ids))
        if task_id:
            stmt = stmt.where(AlertGroup.task_id == task_id)
        if status:
            stmt = stmt.where(AlertGroup.status == status)
        if resolution:
            stmt = stmt.where(AlertGroup.resolution == resolution)
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
        needle = (q or "").strip()
        if needle:
            pattern = f"%{needle}%"
            stmt = stmt.where(or_(
                AlertGroup.cwe.ilike(pattern),
                AlertGroup.file_path.ilike(pattern),
                AlertGroup.function_symbol.ilike(pattern),
                AlertGroup.task_id.ilike(pattern),
                Task.project_address.ilike(pattern),
            ))
        return stmt

    async def list_groups(
        self, *, task_id: str | None = None, status: str | None = None,
        resolution: str | None = None,
        cwe: str | None = None, ai_verdict: str | None = None,
        engine: str | None = None, clue_grade: str | None = None,
        scope: str | None = None, q: str | None = None,
        limit: int = 50, offset: int = 0, owner_id: str | None = None,
        owner_task_ids: list[str] | None = None,
    ) -> tuple[int, list[AlertGroup]]:
        stmt = self._filtered_groups_stmt(
            task_id=task_id, status=status, resolution=resolution, cwe=cwe,
            ai_verdict=ai_verdict, engine=engine, clue_grade=clue_grade,
            scope=scope, q=q, owner_id=owner_id, owner_task_ids=owner_task_ids,
        )
        total = (await self.session.execute(
            select(func.count()).select_from(
                stmt.with_only_columns(AlertGroup.id).order_by(None).distinct().subquery()
            )
        )).scalar_one()
        # 复核台按「最近更新」优先（规格 §3.5）；并列用创建 时间
        rows = await self.session.execute(
            stmt.order_by(
                AlertGroup.updated_at.desc().nulls_last(),
                AlertGroup.created_at.desc(),
            ).limit(limit).offset(offset)
        )
        return int(total), list(rows.scalars().unique().all())

    async def list_group_ids(
        self, *, task_id: str | None = None, status: str | None = None,
        resolution: str | None = None,
        cwe: str | None = None, ai_verdict: str | None = None,
        engine: str | None = None, clue_grade: str | None = None,
        scope: str | None = None, q: str | None = None,
        owner_id: str | None = None,
        owner_task_ids: list[str] | None = None,
        max_ids: int = MAX_CROSS_PAGE_IDS,
    ) -> tuple[int, list[str]]:
        """当前筛选下全部 id（跨页全选）。超出 max_ids 抛 ValueError。"""
        stmt = self._filtered_groups_stmt(
            task_id=task_id, status=status, resolution=resolution, cwe=cwe,
            ai_verdict=ai_verdict, engine=engine, clue_grade=clue_grade,
            scope=scope, q=q, owner_id=owner_id, owner_task_ids=owner_task_ids,
        )
        total = int((await self.session.execute(
            select(func.count()).select_from(
                stmt.with_only_columns(AlertGroup.id).order_by(None).distinct().subquery()
            )
        )).scalar_one())
        if total > max_ids:
            raise ValueError(
                f"筛选结果共 {total} 条，超过跨页全选上限 {max_ids}，请先收窄筛选"
            )
        rows = await self.session.execute(
            stmt.order_by(
                AlertGroup.updated_at.desc().nulls_last(),
                AlertGroup.created_at.desc(),
            )
        )
        return total, [g.id for g in rows.scalars().unique().all()]

    @staticmethod
    def engine_member_clause(engine: str):
        """engine_set JSON 数组按元素精确包含："osv" 不命中 "osv-scanner"。"""
        return _JsonArrayHas(AlertGroup.engine_set, engine)

    @staticmethod
    def queue_scope_clause(scope: str):
        """工作队列：验证中、已确认、代码可达；workbench 为其并集。"""
        verifying = AlertGroup.status == "dispatched"
        confirmed = and_(
            AlertGroup.status == "resolved",
            AlertGroup.resolution.in_(("confirmed", "partial")),
        )
        reachable = and_(
            AlertGroup.status == "resolved",
            AlertGroup.resolution == "code_reachable",
        )
        clauses = {
            "verifying": verifying,
            "confirmed": confirmed,
            "reachable": reachable,
            "workbench": or_(verifying, confirmed, reachable),
        }
        return clauses[scope]

    async def group_queue_stats(self, owner_id: str) -> dict[str, int]:
        verifying = self.queue_scope_clause("verifying")
        confirmed = self.queue_scope_clause("confirmed")
        reachable = self.queue_scope_clause("reachable")
        workbench = self.queue_scope_clause("workbench")
        row = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(case((workbench, 1), else_=0)), 0),
                    func.coalesce(func.sum(case((verifying, 1), else_=0)), 0),
                    func.coalesce(func.sum(case((confirmed, 1), else_=0)), 0),
                    func.coalesce(func.sum(case((reachable, 1), else_=0)), 0),
                )
                .select_from(AlertGroup)
                .join(Task, Task.id == AlertGroup.task_id)
                .where(Task.owner_id == owner_id)
            )
        ).one()
        return {
            "workbench": int(row[0] or 0),
            "verifying": int(row[1] or 0),
            "confirmed": int(row[2] or 0),
            "reachable": int(row[3] or 0),
        }

    async def group_stats(
        self, owner_id: str,
    ) -> tuple[dict[str, int], dict[str, int]]:
        status_rows = await self.session.execute(
            select(AlertGroup.status, func.count(AlertGroup.id))
            .join(Task, Task.id == AlertGroup.task_id)
            .where(Task.owner_id == owner_id)
            .group_by(AlertGroup.status)
        )
        resolution_rows = await self.session.execute(
            select(AlertGroup.resolution, func.count(AlertGroup.id))
            .join(Task, Task.id == AlertGroup.task_id)
            .where(
                Task.owner_id == owner_id,
                AlertGroup.resolution.is_not(None),
            )
            .group_by(AlertGroup.resolution)
        )
        return (
            {str(status): int(count) for status, count in status_rows.all()},
            {str(resolution): int(count) for resolution, count in resolution_rows.all()},
        )

    async def list_group_members(self, group: AlertGroup) -> list[RawFinding]:
        bound = await self.session.execute(
            select(RawFinding).where(RawFinding.alert_group_id == group.id).order_by(
                RawFinding.created_at
            )
        )
        members = list(bound.scalars().all())
        if members:
            return members
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
