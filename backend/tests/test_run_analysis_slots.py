"""无槽时 retry，不得把 queued 改成 running。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from celery.exceptions import Retry
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from unittest.mock import AsyncMock, patch

from app.shared.base import Base


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.project.models import Project  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import Credential, LlmProvider, PlatformSetting  # noqa: F401
        from app.contexts.task.models import AgentEvent, NodeRun, Task, TaskRun  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


class _RetryingCelery:
    def retry(self, countdown=15):
        raise Retry("waiting for slot")


@pytest.mark.asyncio
async def test_no_slot_retries_without_claiming(session_factory):
    from app.contexts.agent.tasks import admit_task_run
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
        await session.commit()
        task_id, run_id = task.id, run.id

        with patch(
            "app.contexts.agent.tasks.try_acquire_slot",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with pytest.raises(Retry):
                await admit_task_run(session, _RetryingCelery(), task_id, run_id)

        await session.refresh(task)
        await session.refresh(run)
        assert task.status == "queued"
        assert run.status == "pending"


@pytest.mark.asyncio
async def test_cancelled_task_skips_slot(session_factory):
    from app.contexts.agent.tasks import admit_task_run
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

        acquire = AsyncMock()
        with patch("app.contexts.agent.tasks.try_acquire_slot", acquire):
            task_out, run_out, err, acquired = await admit_task_run(
                session, _RetryingCelery(), task.id, run.id
            )
        acquire.assert_not_awaited()
        assert acquired is False
        assert err is not None
        assert task_out is not None
        assert task_out.status == "cancelled"
