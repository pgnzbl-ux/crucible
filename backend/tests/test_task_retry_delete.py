"""任务 retry（从节点 0 整条重跑）测试。"""
import asyncio
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
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.project.models import Project  # noqa: F401
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_creates_new_run_without_reusing_nodes(session_factory):
    """retry 新建 run，不拷贝上一 run 的 NodeRun，从源码获取整条重跑。"""
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
        with patch("app.core.celery_app.celery_app.send_task"):
            new_run_id = await svc.retry_task(task.id, "u1")

        from sqlalchemy import select
        new_nodes = (
            await session.execute(
                select(NodeRun).where(NodeRun.run_id == new_run_id).order_by(NodeRun.node_index)
            )
        ).scalars().all()
        assert new_run_id != old_run.id
        assert new_nodes == []
        old_nodes = (
            await session.execute(select(NodeRun).where(NodeRun.run_id == old_run.id))
        ).scalars().all()
        assert len(old_nodes) == 3


@pytest.mark.asyncio
async def test_retry_marks_task_queued_until_worker_starts(session_factory):
    """重试后任务应排队，不能提前标 running，否则列表误显「分析中」。"""
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch
    from sqlalchemy import select

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="failed"))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.send_task"):
            new_run_id = await svc.retry_task(task.id, "u1")

        await session.refresh(task)
        new_run = (
            await session.execute(select(TaskRun).where(TaskRun.id == new_run_id))
        ).scalar_one()
        assert task.status == "queued"
        assert new_run.status == "pending"


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
            await svc.retry_task(task.id, "u1")


@pytest.mark.asyncio
async def test_task_operations_require_owner(session_factory):
    """已知 UUID 也不能读取或重试其他用户的任务。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="secret",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))

        assert await svc.get_task(task.id, "u2") is None
        with pytest.raises(ValueError, match="任务不存在"):
            await svc.retry_task(task.id, "u2")


@pytest.mark.asyncio
async def test_cancel_marks_running_and_pending_nodes_cancelled(session_factory):
    """取消任务后，正在跑/未开始的节点必须离开 running，进度条不能继续转圈。"""
    from app.contexts.task.models import Task, TaskRun, NodeRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock
    from sqlalchemy import select

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="running")
        session.add(run)
        await session.flush()
        for i, (key, st) in enumerate(
            [
                ("source", "completed"),
                ("profile", "completed"),
                ("env_ready", "running"),
                ("audit", "pending"),
            ]
        ):
            session.add(
                NodeRun(
                    run_id=run.id,
                    task_id=task.id,
                    node_index=i,
                    node_key=key,
                    status=st,
                    output_json="{}",
                )
            )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.control.revoke"), patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ):
            detail = await svc.cancel_task(task.id, "u1")
            await asyncio.sleep(0)

        assert detail is not None
        assert detail.status == "cancelled"
        nodes = (
            await session.execute(select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index))
        ).scalars().all()
        by_key = {n.node_key: n for n in nodes}
        assert by_key["source"].status == "completed"
        assert by_key["profile"].status == "completed"
        assert by_key["env_ready"].status == "cancelled"
        assert by_key["env_ready"].finished_at is not None
        assert by_key["audit"].status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_tears_down_lab_and_runner(session_factory):
    """取消不能只改库：必须拆靶场 compose 和该任务的 agent-runner。"""
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="running"))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.control.revoke"), patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ) as mock_teardown:
            await svc.cancel_task(task.id, "u1")
            await asyncio.sleep(0)

        mock_teardown.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_cancel_returns_before_teardown_finishes(session_factory):
    """取消 HTTP 不能等 compose down：库标 cancelled 后立刻返回，拆容器后台做。"""
    from app.contexts.task.models import Task, TaskRun
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch

    hang = asyncio.Event()

    async def never_finishes(_task_id: str) -> None:
        await hang.wait()

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        session.add(TaskRun(task_id=task.id, status="running"))
        await session.flush()

        svc = TaskService(TaskRepository(session))
        with patch("app.core.celery_app.celery_app.control.revoke"), patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            never_finishes,
        ):
            detail = await asyncio.wait_for(svc.cancel_task(task.id, "u1"), timeout=0.5)

        assert detail is not None
        assert detail.status == "cancelled"
        hang.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_delete_tears_down_lab_and_runner(session_factory):
    """删除已结束任务时也要拆残留靶场，避免历史容器堆积。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock

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
        with patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ) as mock_teardown:
            ok = await svc.delete_task(task.id, "u1")
        assert ok is True
        mock_teardown.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_soft_delete_archives(session_factory):
    """软删把 status 改 archived,不删行。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService
    from unittest.mock import patch, AsyncMock

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
        with patch(
            "app.contexts.agent.runtime_cleanup.teardown_task_runtime",
            new_callable=AsyncMock,
        ) as mock_teardown:
            ok = await svc.delete_task(task.id, "u1")
        assert ok is True
        assert task.status == "archived"
        mock_teardown.assert_awaited_once_with(task.id)


@pytest.mark.asyncio
async def test_soft_delete_rejects_already_archived(session_factory):
    """已归档任务不能再软删（避免前端反复点删除都提示成功）。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="archived",
        )
        session.add(task)
        await session.flush()
        svc = TaskService(TaskRepository(session))
        with pytest.raises(ValueError, match="已归档"):
            await svc.delete_task(task.id, "u1")
