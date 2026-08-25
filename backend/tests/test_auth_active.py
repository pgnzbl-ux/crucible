"""JWT 鉴权必须拒绝停用账号。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest_asyncio.fixture
async def client_env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from app.core.database import get_db_session
    from app.main import create_app

    app = create_app()

    async def _override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_inactive_user_jwt_is_rejected(client_env):
    from app.contexts.identity.models import User
    from app.core.security import create_access_token

    client, factory = client_env
    async with factory() as session:
        session.add(User(
            id="u-off", email="off@x.test", password_hash="x",
            display_name="Off", is_active=False,
        ))
        await session.commit()
    headers = {"Authorization": f"Bearer {create_access_token('u-off', 'off@x.test')}"}
    resp = client.get("/api/v1/findings/stats", headers=headers)
    assert resp.status_code == 401
