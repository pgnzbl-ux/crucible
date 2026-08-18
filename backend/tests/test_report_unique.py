"""同一 run 只能有一份 Report；冲突时复用已有行。"""
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
async def test_ensure_report_for_run_returns_existing_on_conflict(session_factory):
    from app.contexts.agent.tasks import ensure_report_for_run
    from app.contexts.report.models import Report
    from app.contexts.task.models import Task, TaskRun

    async with session_factory() as session:
        task = Task(project_address="x", vulnerability_description="d", owner_id="u1")
        session.add(task)
        await session.flush()
        run = TaskRun(task_id=task.id, status="completed")
        session.add(run)
        await session.flush()

        first = Report(
            task_id=task.id,
            run_id=run.id,
            owner_id="u1",
            status="generated",
            title="first",
        )
        got = await ensure_report_for_run(session, first)
        await session.commit()
        assert got.id == first.id

        second = Report(
            task_id=task.id,
            run_id=run.id,
            owner_id="u1",
            status="generated",
            title="second",
        )
        reused = await ensure_report_for_run(session, second)
        await session.commit()
        assert reused.id == first.id
        assert reused.title == "first"
