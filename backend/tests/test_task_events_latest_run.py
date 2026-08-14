"""事件流默认只返回最新一次 run，避免重试把历史「环境准备」堆在一起。"""
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
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_events_defaults_to_latest_run_only(session_factory):
    from app.contexts.task.models import Task, TaskRun, AgentEvent
    from app.contexts.task.repository import TaskRepository
    from app.contexts.task.service import TaskService

    async with session_factory() as session:
        task = Task(
            project_address="https://github.com/acme/x",
            vulnerability_description="d",
            owner_id="u1",
            status="failed",
        )
        session.add(task)
        await session.flush()

        older = datetime.now(timezone.utc) - timedelta(hours=2)
        newer = datetime.now(timezone.utc)
        old_run = TaskRun(task_id=task.id, status="failed", created_at=older)
        new_run = TaskRun(task_id=task.id, status="running", created_at=newer)
        session.add_all([old_run, new_run])
        await session.flush()

        session.add(
            AgentEvent(
                run_id=old_run.id,
                task_id=task.id,
                sequence=1,
                event_type="phase.updated",
                payload='{"type":"phase.updated","phase":"preflight","message":"在 host 上克隆源码"}',
            )
        )
        session.add(
            AgentEvent(
                run_id=new_run.id,
                task_id=task.id,
                sequence=1,
                event_type="phase.updated",
                payload='{"type":"phase.updated","phase":"preflight","message":"创建工作区，源码由节点 0 获取"}',
            )
        )
        await session.flush()

        svc = TaskService(TaskRepository(session))
        events = await svc.get_task_events(task.id)

        assert len(events) == 1
        assert events[0]["run_id"] == new_run.id
        assert "创建工作区" in events[0]["payload"]["message"]


def test_should_persist_skips_thinking_tokens_heartbeat():
    from app.contexts.agent.tasks import should_persist_agent_event

    assert should_persist_agent_event(
        {"type": "phase.updated", "phase": "start", "message": "init"}
    )
    assert not should_persist_agent_event(
        {"type": "phase.updated", "phase": "start", "message": "thinking_tokens"}
    )
    assert should_persist_agent_event({"type": "agent.thinking", "text": "..."})
