"""任务 retry(断点续跑)测试。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.project.models import Project  # noqa: F401
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_creates_new_run_and_reuses_completed_nodes(session_factory):
    """retry 新建 run,旧 run 已完成的 NodeRun 拷为新 run 的 completed 节点(断点续跑)。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        old_run = TaskRun(task_id=task.id, status="failed")
        session.add(old_run)
        await session.flush()
        # 旧 run: 节点 0/1 completed, 节点 2 failed
        for i, (key, st) in enumerate(
            [("source", "completed"), ("profile", "completed"), ("env_ready", "failed")]
        ):
            session.add(
                NodeRun(
                    run_id=old_run.id,
                    task_id=task.id,
                    node_index=i,
                    node_key=key,
                    status=st,
                    output_json='{"x":1}' if st == "completed" else "{}",
                )
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        # mock Celery send_task(避免真连 broker)
        with patch("app.core.celery_app.celery_app.send_task"):
            new_run_id = await svc.retry_task(task.id)

        # 新 run: 节点 0/1 应 completed(复用),节点 2 pending
        from sqlalchemy import select
        new_nodes = (
            await session.execute(
                select(NodeRun).where(NodeRun.run_id == new_run_id).order_by(NodeRun.node_index)
            )
        ).scalars().all()
        assert len(new_nodes) == 2  # 只有 completed 的 0/1 被复刻
        assert new_nodes[0].status == "completed"
        assert new_nodes[0].output_json == '{"x":1}'
        assert new_nodes[1].status == "completed"


@pytest.mark.asyncio
async def test_retry_rejects_running_task(session_factory):
    """running 中的任务不能 retry(应先 cancel 或等完成)。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with pytest.raises(ValueError, match="不能重试"):
            await svc.retry_task(task.id)


@pytest.mark.asyncio
async def test_soft_delete_archives(session_factory):
    """软删把 status 改 archived,不删行。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="completed",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))
        ok = await svc.delete_task(task.id)
        assert ok is True
        assert task.status == "archived"
