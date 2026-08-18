"""Worker 认领任务：已取消/归档不得改回 running。"""
import os
import sys

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
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        from app.contexts.task.models import AgentEvent, NodeRun, Task, TaskRun  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "archived", "completed", "failed", "needs_review"])
async def test_claim_refuses_terminal_task(session_factory, status):
    from app.contexts.agent.tasks import claim_task_run
    from app.contexts.task.models import Task, TaskRun

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status=status,
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="pending")
        session.add(run)
        await session.flush()

        claimed_task, claimed_run, err = await claim_task_run(session, task.id, run.id)
        assert claimed_task is None
        assert claimed_run is None
        assert err is not None
        assert status in err
        await session.refresh(task)
        await session.refresh(run)
        assert task.status == status
        assert run.status == "pending"


@pytest.mark.asyncio
async def test_claim_allows_queued_task(session_factory):
    from app.contexts.agent.tasks import claim_task_run
    from app.contexts.task.models import Task, TaskRun

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="queued",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="pending")
        session.add(run)
        await session.flush()

        claimed_task, claimed_run, err = await claim_task_run(session, task.id, run.id)
        assert err is None
        assert claimed_task is not None
        assert claimed_run is not None
        assert claimed_task.status == "running"
        assert claimed_run.status == "running"
