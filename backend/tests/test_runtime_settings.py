"""平台运行时设置：并行上限 get-or-create + 校验。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base
from app.shared.exception_handlers import register_exception_handlers


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


@pytest.mark.asyncio
async def test_get_runtime_inserts_default_one(session_factory):
    from sqlalchemy import func, select

    from app.contexts.settings.models import PlatformSetting
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    async with session_factory() as session:
        svc = SettingsService(SettingsRepository(session))
        got = await svc.get_runtime_settings()
        assert got.max_concurrent_tasks == 1
        again = await svc.get_runtime_settings()
        assert again.max_concurrent_tasks == 1
        n = (await session.execute(select(func.count()).select_from(PlatformSetting))).scalar()
        assert n == 1


@pytest.mark.asyncio
async def test_update_runtime_persists(session_factory):
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    async with session_factory() as session:
        svc = SettingsService(SettingsRepository(session))
        updated = await svc.update_runtime_settings(2)
        assert updated.max_concurrent_tasks == 2
        got = await svc.get_runtime_settings()
        assert got.max_concurrent_tasks == 2
        assert got.max_allowed >= 2


@pytest.mark.parametrize("value", [0, 9, -1])
def test_update_request_rejects_out_of_range(value):
    from app.contexts.settings.schemas import RuntimeSettingsUpdateRequest

    with pytest.raises(ValidationError):
        RuntimeSettingsUpdateRequest(max_concurrent_tasks=value)


def test_update_request_forbids_extra_fields():
    from app.contexts.settings.schemas import RuntimeSettingsUpdateRequest

    with pytest.raises(ValidationError):
        RuntimeSettingsUpdateRequest(max_concurrent_tasks=1, other=True)


def test_runtime_get_requires_authentication(monkeypatch):
    from app.contexts.settings.api import router
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "environment", "production")
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    response = TestClient(app).get("/api/v1/settings/runtime")
    assert response.status_code == 401


def test_runtime_put_returns_max_allowed():
    from app.contexts.settings.api import get_settings_service, router
    from app.contexts.settings.schemas import RuntimeSettingsResponse
    from app.shared.deps import get_current_user_id

    class _Fake:
        async def update_runtime_settings(self, n: int) -> RuntimeSettingsResponse:
            return RuntimeSettingsResponse(
                max_concurrent_tasks=n,
                max_allowed=4,
                worker_pool="prefork",
            )

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_settings_service] = lambda: _Fake()
    app.dependency_overrides[get_current_user_id] = lambda: "u1"
    response = TestClient(app).put("/api/v1/settings/runtime", json={"max_concurrent_tasks": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["max_concurrent_tasks"] == 2
    assert body["max_allowed"] == 4
    assert body["worker_pool"] == "prefork"
