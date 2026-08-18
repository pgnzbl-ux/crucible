"""空库只能创建第一个账号；之后禁止自行注册。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contexts.identity.models import User
from app.contexts.identity.repository import IdentityRepository
from app.contexts.identity.schemas import RegisterRequest
from app.contexts.identity.service import IdentityService


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(User.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_register_request_rejects_is_admin_field():
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(
            {
                "email": "a@example.com",
                "password": "password1",
                "display_name": "A",
                "is_admin": True,
            }
        )


@pytest.mark.asyncio
async def test_first_account_is_created_then_self_register_blocked(session):
    svc = IdentityService(IdentityRepository(session))
    assert await svc.needs_setup() is True

    first = await svc.register(
        RegisterRequest(email="op@example.com", password="password1", display_name="Op")
    )
    assert first.is_admin is True
    assert first.role == "admin"
    assert await svc.needs_setup() is False

    with pytest.raises(PermissionError, match="禁止自行注册"):
        await svc.register(
            RegisterRequest(email="other@example.com", password="password1", display_name="Other")
        )
