"""任务列表筛选：多状态、关键词、日期范围。"""
import sys
import os
from datetime import datetime, timedelta, timezone

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
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed(session: AsyncSession) -> None:
    from app.contexts.task.models import Task

    now = datetime.now(timezone.utc)
    rows = [
        ("pending", "https://github.com/acme/alpha", now - timedelta(days=1)),
        ("queued", "https://github.com/acme/beta", now - timedelta(days=2)),
        ("running", "https://github.com/other/gamma", now - timedelta(days=10)),
    ]
    for status, addr, created in rows:
        t = Task(
            project_address=addr,
            vulnerability_description="desc-" + status,
            owner_id="u1",
            status=status,
            priority="medium",
        )
        session.add(t)
        await session.flush()
        t.created_at = created
    await session.flush()


@pytest.mark.asyncio
async def test_list_supports_comma_separated_statuses(session_factory):
    from app.contexts.task.repository import TaskRepository

    async with session_factory() as session:
        await _seed(session)
        repo = TaskRepository(session)
        items, total = await repo.list_by_owner("u1", status="pending,queued")
        assert total == 2
        assert {t.status for t in items} == {"pending", "queued"}


@pytest.mark.asyncio
async def test_list_filters_by_q_and_date_range(session_factory):
    from app.contexts.task.repository import TaskRepository

    async with session_factory() as session:
        await _seed(session)
        repo = TaskRepository(session)
        now = datetime.now(timezone.utc)
        items, total = await repo.list_by_owner(
            "u1",
            q="acme",
            date_from=(now - timedelta(days=3)).date().isoformat(),
            date_to=now.date().isoformat(),
        )
        assert total == 2
        assert all("acme" in t.project_address for t in items)


@pytest.mark.asyncio
async def test_list_excludes_archived_by_default(session_factory):
    """未指定 status 时，已归档任务不出现在默认列表。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository

    async with session_factory() as session:
        await _seed(session)
        session.add(
            Task(
                project_address="https://github.com/acme/archived",
                vulnerability_description="desc-archived",
                owner_id="u1",
                status="archived",
                priority="medium",
            )
        )
        await session.flush()
        repo = TaskRepository(session)
        items, total = await repo.list_by_owner("u1")
        assert total == 3
        assert all(t.status != "archived" for t in items)


@pytest.mark.asyncio
async def test_list_includes_archived_when_filtered(session_factory):
    """显式 status=archived 时可以查到已归档任务。"""
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository

    async with session_factory() as session:
        await _seed(session)
        session.add(
            Task(
                project_address="https://github.com/acme/archived",
                vulnerability_description="desc-archived",
                owner_id="u1",
                status="archived",
                priority="medium",
            )
        )
        await session.flush()
        repo = TaskRepository(session)
        items, total = await repo.list_by_owner("u1", status="archived")
        assert total == 1
        assert items[0].status == "archived"


@pytest.mark.asyncio
async def test_count_by_status_skips_archived_and_other_owners(session_factory):
    from app.contexts.task.models import Task
    from app.contexts.task.repository import TaskRepository

    async with session_factory() as session:
        await _seed(session)
        session.add(
            Task(
                project_address="https://github.com/acme/archived",
                vulnerability_description="desc-archived",
                owner_id="u1",
                status="archived",
                priority="medium",
            )
        )
        session.add(
            Task(
                project_address="https://github.com/other/x",
                vulnerability_description="desc-other",
                owner_id="u2",
                status="running",
                priority="medium",
            )
        )
        await session.flush()
        counts = await TaskRepository(session).count_by_status("u1")
        assert counts == {"pending": 1, "queued": 1, "running": 1}
        assert "archived" not in counts
