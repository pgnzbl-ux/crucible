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

    async def get_by_id_with_runs(self, task_id: str) -> Task | None:
        result = await self.session.execute(
            select(Task)
            .where(Task.id == task_id)
            .options(selectinload(Task.runs))
        )
        return result.scalar_one_or_none()

    async def list_by_owner(
        self,
        owner_id: str,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        stmt = select(Task).where(Task.owner_id == owner_id)
        count_stmt = select(func.count(Task.id)).where(Task.owner_id == owner_id)

        if status:
            stmt = stmt.where(Task.status == status)
            count_stmt = count_stmt.where(Task.status == status)
        if priority:
            stmt = stmt.where(Task.priority == priority)
            count_stmt = count_stmt.where(Task.priority == priority)

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

    async def get_events_for_task(self, task_id: str, limit: int = 200) -> list[AgentEvent]:
        """任务全部 Agent 事件（按 sequence 升序）"""
        result = await self.session.execute(
            select(AgentEvent)
            .where(AgentEvent.task_id == task_id)
            .order_by(AgentEvent.sequence.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

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
