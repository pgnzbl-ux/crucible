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


@pytest.mark.asyncio
async def test_claim_refuses_second_run_while_task_already_running(session_factory):
    from app.contexts.agent.tasks import claim_task_run
    from app.contexts.task.models import Task, TaskRun

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="running",
        )
        session.add(task)
        await session.flush()
        live = TaskRun(task_id=task.id, status="running")
        other = TaskRun(task_id=task.id, status="pending")
        session.add_all([live, other])
        await session.flush()

        claimed_task, claimed_run, err = await claim_task_run(session, task.id, other.id)
        assert claimed_task is None
        assert claimed_run is None
        assert err is not None
        await session.refresh(other)
        assert other.status == "pending"
        await session.refresh(live)
        assert live.status == "running"


@pytest.mark.asyncio
async def test_claim_allows_redelivery_of_same_running_run(session_factory):
    from app.contexts.agent.tasks import claim_task_run
    from app.contexts.task.models import Task, TaskRun

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

        claimed_task, claimed_run, err = await claim_task_run(session, task.id, run.id)
        assert err is None
        assert claimed_task is not None
        assert claimed_run is not None
        assert claimed_task.id == task.id
        assert claimed_run.id == run.id
        assert claimed_run.status == "running"


@pytest.mark.asyncio
async def test_apply_analysis_failure_does_not_overwrite_cancelled(session_factory):
    from app.contexts.agent.tasks import apply_analysis_failure
    from app.contexts.task.models import Task, TaskRun

    async with session_factory() as session:
        task = Task(
            project_address="x",
            vulnerability_description="d",
            owner_id="u1",
            status="cancelled",
        )
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="cancelled")
        session.add(run)
        await session.flush()

        await apply_analysis_failure(session, task, run, "boom")
        await session.refresh(task)
        await session.refresh(run)
        assert task.status == "cancelled"
        assert run.status == "cancelled"


@pytest.mark.asyncio
async def test_apply_analysis_failure_after_flush_error_fails_running_node(session_factory):
    """flush 失败后 session 需 rollback，并把仍 running 的 NodeRun 标 failed。"""
    from app.contexts.agent.tasks import apply_analysis_failure
    from app.contexts.project.models import Project
    from app.contexts.task.models import NodeRun, Task, TaskRun

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
        nr = NodeRun(
            run_id=run.id,
            task_id=task.id,
            node_index=1,
            node_key="profile",
            status="running",
            input_json="{}",
        )
        session.add(nr)
        await session.commit()

        session.add(Project(id="dup", name="a", git_url="https://github.com/a/b", owner_id="u1"))
        await session.flush()
        session.add(Project(id="dup", name="b", git_url="https://github.com/a/c", owner_id="u1"))
        try:
            await session.flush()
        except Exception:
            pass

        await apply_analysis_failure(session, task, run, "value too long")
        await session.commit()
        await session.refresh(task)
        await session.refresh(run)
        await session.refresh(nr)
        assert task.status == "failed"
        assert run.status == "failed"
        assert nr.status == "failed"
