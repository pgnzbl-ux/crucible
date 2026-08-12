from datetime import datetime, timezone
from typing import Any
import json

from .models import Task, TaskRun, AgentEvent
from .repository import TaskRepository
from .schemas import TaskCreateRequest, TaskDetail, TaskListResponse, TaskSummary, RunSummary


def _parse_refs(refs_raw: str | None) -> list[str]:
    """credential_refs JSON 字符串 → list（容错）"""
    if not refs_raw:
        return []
    try:
        v = json.loads(refs_raw)
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


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
            project_id=getattr(task, "project_id", None),
            project_ref=task.project_ref,
            source_type=task.source_type,
            vulnerability_description=task.vulnerability_description,
            vulnerability_reasoning=task.vulnerability_reasoning,
            status=task.status,
            verdict=getattr(task, "verdict", None),
            priority=task.priority,
            owner_id=task.owner_id,
            credential_refs=_parse_refs(getattr(task, "credential_refs", None)),
            created_at=task.created_at,
            updated_at=task.updated_at,
            runs=[RunSummary.model_validate(r) for r in (runs or [])],
        )

    async def create_task(self, request: TaskCreateRequest, owner_id: str) -> TaskDetail:
        # 自动按 git_url 建/复用 Project(用户建任务时无需先建 project)
        project_id: str | None = None
        try:
            from app.contexts.project.repository import ProjectRepository
            from app.contexts.project.service import ProjectService

            proj_svc = ProjectService(ProjectRepository(self.repo.session))
            project = await proj_svc.upsert_by_git_url(
                git_url=request.project_address,
                owner_id=owner_id,
                default_ref=request.project_ref,
            )
            project_id = project.id
        except Exception:  # noqa: BLE001 — project 建失败不阻断建任务(回退 project_address)
            pass

        task = Task(
            project_address=request.project_address,
            project_id=project_id,
            project_ref=request.project_ref,
            source_type=request.source_type,
            vulnerability_description=request.vulnerability_description,
            vulnerability_reasoning=request.vulnerability_reasoning,
            priority=request.priority,
            status="queued",
            owner_id=owner_id,
            credential_refs=json.dumps(request.credential_refs or [], ensure_ascii=False),
        )
        task = await self.repo.create(task)

        # 创建首次运行记录并异步提交给 Agent worker
        run = TaskRun(task_id=task.id, status="pending")
        run = await self.repo.create_run(run)

        # 触发 Celery 任务（agent context）— 不阻塞创建响应
        # 用 run.id 作为 celery task_id，cancel 时可精确 revoke（terminate → SIGTERM → 容器销毁）
        from app.core.celery_app import celery_app

        try:
            celery_app.send_task("agent.run_analysis", args=[task.id, run.id], task_id=run.id)
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
        """取消任务：状态机校验 → revoke 所有活跃 celery 任务 → 标记 cancelled。

        revoke(terminate=True, signal=SIGTERM) 触发 agent/tasks.py::_on_sigterm 钩子，
        自动销毁 agent-runner 容器（双重保险的另一端是 cleanup_stale 巡检）。
        """
        task = await self.repo.get_by_id_with_runs(task_id)
        if not task:
            return None
        if task.status not in ("pending", "queued", "running"):
            raise ValueError(f"状态为 {task.status} 的任务不能取消")

        # 1. 标记所有活跃 run 为 cancelled（并取出列表用于 revoke）
        cancelled_runs = await self.repo.cancel_active_runs(task_id)

        # 2. revoke 对应 celery 任务（task_id = run.id，见 create_task）
        from app.core.celery_app import celery_app

        for run in cancelled_runs:
            try:
                celery_app.control.revoke(run.id, terminate=True, signal="SIGTERM")
            except Exception:
                # broker 不可用不能阻断 DB 状态更新（任务已标 cancelled，worker 侧 SIGTERM 钩子兜底）
                pass

        # 3. 任务整体状态 → cancelled
        await self.repo.update_status(task, "cancelled")
        return self._to_detail(task, task.runs)

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

    async def retry_task(self, task_id: str) -> str:
        """重试任务:新建 TaskRun,复用上一 run 已完成的 NodeRun.output_json(断点续跑),
        从第一个非 completed 节点起重跑。返回新 run_id。

        复用策略:遍历上一 run 的 NodeRun,把 status=completed 的节点以 completed 形态
        拷进新 run(保留 output_json),这样编排器从失败节点起重跑,之前的不重算。
        """
        from app.contexts.task.models import NodeRun
        from sqlalchemy import select as sa_select

        task = await self.repo.get_by_id_with_runs(task_id)
        if not task:
            raise ValueError("任务不存在")
        if task.status not in ("failed", "completed", "cancelled", "needs_review"):
            raise ValueError(f"状态为 {task.status} 的任务不能重试")

        # 最新 run(repo 按 created_at desc 排,第一个即最新)
        last_run = task.runs[0] if task.runs else None
        new_run = TaskRun(task_id=task_id, status="pending")
        new_run = await self.repo.create_run(new_run)

        # 复用上一 run 已完成节点的 output_json
        if last_run:
            result = await self.repo.session.execute(
                sa_select(NodeRun)
                .where(NodeRun.run_id == last_run.id, NodeRun.status == "completed")
                .order_by(NodeRun.node_index)
            )
            for nr in result.scalars().all():
                self.repo.session.add(
                    NodeRun(
                        run_id=new_run.id,
                        task_id=task_id,
                        node_index=nr.node_index,
                        node_key=nr.node_key,
                        status="completed",  # 直接标完成,断点续跑
                        output_json=nr.output_json,
                        input_json=nr.input_json,
                        started_at=nr.started_at,
                        finished_at=nr.finished_at,
                    )
                )
            await self.repo.session.flush()

        task.status = "running"
        await self.repo.session.flush()

        # 投 Celery(用新 run.id 作 celery task_id,cancel 时可精确 revoke)
        from app.core.celery_app import celery_app

        try:
            celery_app.send_task(
                "agent.run_analysis", args=[task_id, new_run.id], task_id=new_run.id
            )
        except Exception:
            pass  # broker 不可用,run 停 pending,前端可见

        return new_run.id

    async def delete_task(self, task_id: str, *, hard: bool = False) -> bool:
        """删除任务。默认软删(status=archived),hard=True 物理删除。

        running 中的任务不能删(先 cancel)。
        """
        task = await self.repo.get_by_id_with_runs(task_id)
        if not task:
            return False
        if task.status == "running":
            raise ValueError("运行中的任务不能删除,请先取消")

        if hard:
            await self.repo.delete_hard(task)
        else:
            task.status = "archived"
            await self.repo.session.flush()
        return True

    async def get_run_nodes(self, task_id: str, run_id: str) -> list[dict]:
        """获取某 run 的 6 节点状态(前端步骤条数据源)。"""
        from app.contexts.task.models import NodeRun
        from sqlalchemy import select as sa_select

        result = await self.repo.session.execute(
            sa_select(NodeRun)
            .where(NodeRun.run_id == run_id, NodeRun.task_id == task_id)
            .order_by(NodeRun.node_index)
        )
        return [
            {
                "id": nr.id,
                "node_index": nr.node_index,
                "node_key": nr.node_key,
                "status": nr.status,
                "attempt": nr.attempt,
                "error_message": nr.error_message,
                "started_at": nr.started_at.isoformat() if nr.started_at else None,
                "finished_at": nr.finished_at.isoformat() if nr.finished_at else None,
            }
            for nr in result.scalars().all()
        ]
