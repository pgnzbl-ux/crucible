from datetime import datetime, timezone

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Task, TaskRun, AgentEvent, NodeRun


class TaskRepository:
    """任务数据访问层 — 纯 SQL，无业务逻辑"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, task: Task) -> Task:
        self.session.add(task)
        await self.session.flush()
        await self.session.refresh(task)
        return task

    async def get_by_id(self, task_id: str) -> Task | None:
        result = await self.session.execute(
            select(Task).where(Task.id == task_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_with_runs(
        self, task_id: str, owner_id: str | None = None
    ) -> Task | None:
        stmt = select(Task).where(Task.id == task_id)
        if owner_id is not None:
            stmt = stmt.where(Task.owner_id == owner_id)
        result = await self.session.execute(stmt.options(selectinload(Task.runs)))
        return result.scalar_one_or_none()

    async def list_by_owner(
        self,
        owner_id: str,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
        q: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> tuple[list[Task], int]:
        from datetime import datetime, timezone

        stmt = select(Task).where(Task.owner_id == owner_id)
        count_stmt = select(func.count(Task.id)).where(Task.owner_id == owner_id)

        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if len(statuses) == 1:
                stmt = stmt.where(Task.status == statuses[0])
                count_stmt = count_stmt.where(Task.status == statuses[0])
            elif statuses:
                stmt = stmt.where(Task.status.in_(statuses))
                count_stmt = count_stmt.where(Task.status.in_(statuses))
        else:
            stmt = stmt.where(Task.status != "archived")
            count_stmt = count_stmt.where(Task.status != "archived")
        if priority:
            stmt = stmt.where(Task.priority == priority)
            count_stmt = count_stmt.where(Task.priority == priority)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(Task.project_address.ilike(pattern))
            count_stmt = count_stmt.where(Task.project_address.ilike(pattern))
        if date_from:
            start = datetime.fromisoformat(date_from).replace(
                hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
            )
            stmt = stmt.where(Task.created_at >= start)
            count_stmt = count_stmt.where(Task.created_at >= start)
        if date_to:
            end = datetime.fromisoformat(date_to).replace(
                hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc
            )
            stmt = stmt.where(Task.created_at <= end)
            count_stmt = count_stmt.where(Task.created_at <= end)

        stmt = stmt.order_by(Task.created_at.desc()).limit(limit).offset(offset)

        result = await self.session.execute(stmt)
        count_result = await self.session.execute(count_stmt)

        return list(result.scalars().all()), count_result.scalar() or 0

    async def update_status(self, task: Task, new_status: str) -> Task:
        task.status = new_status
        await self.session.flush()
        return task

    async def cancel_active_runs(self, task_id: str) -> list[TaskRun]:
        """把任务下所有活跃 run 标记 cancelled（pending/preflight/running）。

        返回被取消的 run 列表（供 service 层进一步 revoke celery 任务）。
        已是终态（completed/failed/cancelled）的 run 不动。
        """
        runs = await self.get_runs_for_task(task_id)
        cancelled: list[TaskRun] = []
        for run in runs:
            if run.status in ("pending", "preflight", "running"):
                run.status = "cancelled"
                cancelled.append(run)
        if cancelled:
            await self.session.flush()
        return cancelled

    async def cancel_incomplete_nodes(self, run_ids: list[str]) -> int:
        """把指定 run 下 pending/running 的 NodeRun 标 cancelled；completed/failed/skipped 不动。"""
        if not run_ids:
            return 0
        result = await self.session.execute(
            select(NodeRun).where(
                NodeRun.run_id.in_(run_ids),
                NodeRun.status.in_(("pending", "running")),
            )
        )
        nodes = result.scalars().all()
        now = datetime.now(timezone.utc)
        for nr in nodes:
            nr.status = "cancelled"
            nr.finished_at = now
            if not nr.error_message:
                nr.error_message = "任务已取消"
        if nodes:
            await self.session.flush()
        return len(nodes)

    async def create_run(self, run: TaskRun) -> TaskRun:
        self.session.add(run)
        await self.session.flush()
        await self.session.refresh(run)
        return run

    async def get_run(self, run_id: str) -> TaskRun | None:
        result = await self.session.execute(
            select(TaskRun).where(TaskRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def get_runs_for_task(self, task_id: str) -> list[TaskRun]:
        result = await self.session.execute(
            select(TaskRun)
            .where(TaskRun.task_id == task_id)
            .order_by(TaskRun.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_events_for_task(self, task_id: str, limit: int = 1000) -> list[AgentEvent]:
        """当前（最新）run 的 Agent 事件。重试会新建 run，历史 run 的 phase.updated 不再混进事件流。"""
        latest_run = (
            await self.session.execute(
                select(TaskRun.id)
                .where(TaskRun.task_id == task_id)
                .order_by(TaskRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if latest_run is None:
            return []
        result = await self.session.execute(
            select(AgentEvent)
            .where(AgentEvent.task_id == task_id, AgentEvent.run_id == latest_run)
            .order_by(AgentEvent.sequence.desc())
            .limit(limit)
        )
        return list(reversed(list(result.scalars().all())))

    async def delete_hard(self, task: Task) -> None:
        """物理删除任务 + 级联清理 runs/nodes/events。

        顺序: events → nodes → runs → task(尊重 FK)。
        SQLite 默认不强制 FK pragma,用手动级联确保干净。
        """
        run_ids = [r.id for r in task.runs] if task.runs else []
        if run_ids:
            await self.session.execute(delete(AgentEvent).where(AgentEvent.run_id.in_(run_ids)))
            await self.session.execute(delete(NodeRun).where(NodeRun.run_id.in_(run_ids)))
            await self.session.execute(delete(TaskRun).where(TaskRun.id.in_(run_ids)))
        # task 级 events(若有 task_id 直接关联但无 run 的孤儿)
        await self.session.execute(delete(AgentEvent).where(AgentEvent.task_id == task.id))
        await self.session.delete(task)
        await self.session.flush()
