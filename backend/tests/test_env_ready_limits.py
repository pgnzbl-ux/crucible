"""env_ready 限制快照（limits.py）：平台设置注入生效 + 读失败退化默认。"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contexts.agent.nodes.env_ready.limits import (
    DEFAULT_LIMITS,
    EnvReadyLimits,
    probe_attempts,
    resolve_limits,
)
from app.shared.base import Base


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.discovery.models import ScanRun  # noqa: F401
        from app.contexts.finding.models import AlertGroup, RawFinding  # noqa: F401
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


@pytest.mark.asyncio
async def test_resolve_limits_reads_platform_settings(session_factory):
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.schemas import RuntimeSettingsUpdateRequest
    from app.contexts.settings.service import SettingsService

    async with session_factory() as session:
        svc = SettingsService(SettingsRepository(session))
        await svc.update_runtime_settings(RuntimeSettingsUpdateRequest(
            env_ready_max_attempts=8,
            env_ready_compose_up_timeout_seconds=900,
            env_ready_compose_wait_seconds=450,
            env_ready_lab_wait_timeout_seconds=3600,
            env_ready_probe_window_seconds=180,
        ))
        await session.commit()  # 快照走独立会话，须先落库

    ctx = SimpleNamespace(session_factory=session_factory, db_session=None)
    got = await resolve_limits(ctx)
    assert got == EnvReadyLimits(
        max_attempts=8,
        compose_up_timeout=900,
        compose_wait=450,
        lab_wait_timeout=3600,
        probe_window=180,
    )
    assert probe_attempts(got) == 60  # 180s 窗口 / 3s 间隔


@pytest.mark.asyncio
async def test_resolve_limits_falls_back_when_db_unreadable():
    class _BoomSession:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *exc):
            return False

    def _boom_factory():
        return _BoomSession()

    ctx = SimpleNamespace(session_factory=_boom_factory, db_session=None)
    assert await resolve_limits(ctx) == DEFAULT_LIMITS
    assert probe_attempts(DEFAULT_LIMITS) == 30  # 默认 90s 窗口 / 3s 间隔


@pytest.mark.asyncio
async def test_resolve_limits_falls_back_without_sessions():
    ctx = SimpleNamespace(session_factory=None, db_session=None)
    assert await resolve_limits(ctx) == DEFAULT_LIMITS
