from datetime import datetime, timezone
from typing import Any

from .models import Task, TaskRun, AgentEvent
from .repository import TaskRepository
from .schemas import TaskCreateRequest, TaskDetail, TaskListResponse, TaskSummary, RunSummary


class TaskService:
    """任务业务逻辑编排"""

    def __init__(self, repo: TaskRepository):
        self.repo = repo
    @staticmethod
    def _to_detail(task: Task, runs: list[TaskRun] | None = None) -> TaskDetail:
        """安全地将 Task ORM 对象转为 TaskDetail，避免 async lazy-load 问题"""
        return TaskDetail(
            id=task.id,
            project_address=task.project_address,
            project_ref=task.project_ref,
            source_type=task.source_type,
            vulnerability_description=task.vulnerability_description,
            vulnerability_reasoning=task.vulnerability_reasoning,
            status=task.status,
            priority=task.priority,
            owner_id=task.owner_id,
            created_at=task.created_at,
            updated_at=task.updated_at,
            runs=[RunSummary.model_validate(r) for r in (runs or [])],
        )

    async def create_task(self, request: TaskCreateRequest, owner_id: str) -> TaskDetail:
        task = Task(
            project_address=request.project_address,
            project_ref=request.project_ref,
            source_type=request.source_type,
            vulnerability_description=request.vulnerability_description,
            vulnerability_reasoning=request.vulnerability_reasoning,
            priority=request.priority,
            status="queued",
            owner_id=owner_id,
        )
        task = await self.repo.create(task)

        # 创建首次运行记录并异步提交给 Agent worker
        run = TaskRun(task_id=task.id, status="pending")
        run = await self.repo.create_run(run)

        # 触发 Celery 任务（agent context）— 不阻塞创建响应
        from app.core.celery_app import celery_app

        try:
            celery_app.send_task("agent.run_analysis", args=[task.id, run.id])
        except Exception:
            # broker 不可用时任务停留在 queued，前端可见
            await self.repo.update_status(task, "queued")

        return self._to_detail(task, [run])

    async def get_task(self, task_id: str) -> TaskDetail | None:
        task = await self.repo.get_by_id_with_runs(task_id)
        if not task:
            return None
        return self._to_detail(task, task.runs)

    async def list_tasks(
        self, owner_id: str, status: str | None = None,
        priority: str | None = None, limit: int = 50, offset: int = 0,
    ) -> TaskListResponse:
        tasks, total = await self.repo.list_by_owner(owner_id, status, priority, limit, offset)
        return TaskListResponse(
            items=[TaskSummary.model_validate(t) for t in tasks],
            total=total, limit=limit, offset=offset,
        )

    async def cancel_task(self, task_id: str) -> TaskDetail | None:
        task = await self.repo.get_by_id(task_id)
        if not task:
            return None
        if task.status not in ("pending", "queued", "running"):
            raise ValueError(f"状态为 {task.status} 的任务不能取消")
        await self.repo.update_status(task, "cancelled")
        return self._to_detail(task)

    async def get_task_events(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        """任务 Agent 事件流（前端进度展示用）"""
        events = await self.repo.get_events_for_task(task_id, limit)
        result: list[dict[str, Any]] = []
        for ev in events:
            try:
                import json as _json

                payload = _json.loads(ev.payload)
            except (ValueError, TypeError):
                payload = {}
            result.append(
                {
                    "id": ev.id,
                    "run_id": ev.run_id,
                    "sequence": ev.sequence,
                    "event_type": ev.event_type,
                    "payload": payload,
                    "source": ev.source,
                    "created_at": ev.created_at.isoformat(),
                }
            )
        return result
