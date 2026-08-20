"""LLM Provider 只以 is_default 表示启用，无独立 enabled。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contexts.settings.models import LlmProvider


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(LlmProvider.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def skip_url_check(monkeypatch):
    async def _ok(url: str) -> str:
        return url

    monkeypatch.setattr("app.contexts.settings.service.validate_public_https_url", _ok)


def _create_req(**kwargs):
    from app.contexts.settings.schemas import LlmProviderCreateRequest

    payload = {
        "name": "DeepSeek",
        "provider_type": "deepseek",
        "base_url": "https://api.deepseek.com/anthropic",
        "api_key": "sk-test-key",
        "model": "deepseek-v4-flash",
    }
    payload.update(kwargs)
    return LlmProviderCreateRequest(**payload)


def test_provider_schemas_have_no_enabled_field():
    from app.contexts.settings.schemas import (
        LlmProviderCreateRequest,
        LlmProviderResponse,
        LlmProviderUpdateRequest,
    )

    assert "enabled" not in LlmProviderCreateRequest.model_fields
    assert "enabled" not in LlmProviderUpdateRequest.model_fields
    assert "enabled" not in LlmProviderResponse.model_fields
    assert "is_default" in LlmProviderResponse.model_fields


def test_to_response_omits_enabled():
    from app.contexts.settings.models import LlmProvider
    from app.contexts.settings.service import to_response

    now = datetime.now(timezone.utc)
    provider = LlmProvider(
        id="p1",
        name="test",
        provider_type="deepseek",
        base_url="https://api.deepseek.com/anthropic",
        api_key_encrypted="sk-test-1234567890abcdef",
        model="deepseek-v4-flash",
        timeout_ms=600000,
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    dumped = to_response(provider).model_dump()
    assert "enabled" not in dumped
    assert dumped["is_default"] is True


def test_normalize_provider_type_maps_legacy_openai_compat():
    from app.contexts.settings.schemas import normalize_provider_type
    from app.contexts.settings.service import to_response

    now = datetime.now(timezone.utc)
    provider = LlmProvider(
        id="p2",
        name="legacy",
        provider_type="openai_compat",
        base_url="https://proxy.example/anthropic",
        api_key_encrypted="sk-test",
        model="claude-sonnet-4",
        timeout_ms=600000,
        is_default=False,
        created_at=now,
        updated_at=now,
    )
    assert normalize_provider_type("openai_compat") == "custom"
    assert to_response(provider).provider_type == "custom"


def test_create_rejects_openai_compat_provider_type():
    from pydantic import ValidationError

    from app.contexts.settings.schemas import LlmProviderCreateRequest

    with pytest.raises(ValidationError):
        LlmProviderCreateRequest(
            name="bad",
            provider_type="openai_compat",
            base_url="https://api.example.com/anthropic",
            api_key="sk-test",
            model="claude-sonnet-4",
        )


@pytest.mark.asyncio
async def test_first_provider_becomes_default(session, skip_url_check):
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    svc = SettingsService(SettingsRepository(session))
    created = await svc.create_provider(_create_req(is_default=False))
    assert created.is_default is True
    default = await svc.get_default_provider()
    assert default is not None
    assert default.id == created.id


@pytest.mark.asyncio
async def test_second_provider_stays_standby_until_activated(session, skip_url_check):
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    svc = SettingsService(SettingsRepository(session))
    first = await svc.create_provider(_create_req(name="A"))
    second = await svc.create_provider(_create_req(name="B", model="deepseek-v4-pro"))

    assert first.is_default is True
    assert second.is_default is False
    default = await svc.get_default_provider()
    assert default is not None
    assert default.id == first.id

    activated = await svc.activate_provider(second.id)
    assert activated is not None
    assert activated.is_default is True
    default = await svc.get_default_provider()
    assert default is not None
    assert default.id == second.id
    items, _ = await svc.list_providers()
    defaults = [p.id for p in items if p.is_default]
    assert defaults == [second.id]


@pytest.mark.asyncio
async def test_create_with_is_default_clears_previous(session, skip_url_check):
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    svc = SettingsService(SettingsRepository(session))
    first = await svc.create_provider(_create_req(name="A"))
    second = await svc.create_provider(_create_req(name="B", is_default=True))

    items, _ = await svc.list_providers()
    by_id = {p.id: p.is_default for p in items}
    assert by_id[first.id] is False
    assert by_id[second.id] is True
