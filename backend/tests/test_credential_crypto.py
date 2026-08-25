"""凭据存储：Fernet 落库，存量明文可读，注入容器的是明文。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def test_build_env_returns_plaintext_from_legacy_or_sealed():
    """DeepSeek 默认只把明文注入 Bearer token；ORM 里可以是明文或密文。"""
    from app.contexts.settings.models import (
        DEFAULT_LLM_EFFORT,
        DEFAULT_LLM_MAX_CONTEXT_TOKENS,
        LlmProvider,
    )
    from app.contexts.settings.service import SettingsService
    from app.core.crypto import seal_secret

    real_key = "sk-test-1234567890abcdef"
    provider = LlmProvider(
        id="p1",
        name="test",
        provider_type="deepseek",
        base_url="https://api.deepseek.com/anthropic",
        api_key_encrypted=real_key,
        model="deepseek-v4-flash",
        timeout_ms=600000,
        is_default=True,
    )

    svc = SettingsService.__new__(SettingsService)
    env = svc.build_env_from_provider(provider)
    assert env["ANTHROPIC_AUTH_TOKEN"] == real_key
    assert "ANTHROPIC_API_KEY" not in env
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == str(DEFAULT_LLM_MAX_CONTEXT_TOKENS)
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == DEFAULT_LLM_EFFORT
    assert env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] == "1"
    assert env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "0"

    provider.api_key_encrypted = seal_secret(real_key)
    assert provider.api_key_encrypted != real_key
    env2 = svc.build_env_from_provider(provider)
    assert env2["ANTHROPIC_AUTH_TOKEN"] == real_key


def test_reveal_secret_roundtrip_and_legacy_plaintext():
    from app.core.crypto import reveal_secret, seal_secret

    plain = "sk-legacy-plain"
    sealed = seal_secret(plain)
    assert sealed != plain
    assert reveal_secret(sealed) == plain
    assert reveal_secret(plain) == plain
    assert reveal_secret("") == ""


def test_to_response_masks_key():
    """mask_secret 返回掩码,不含完整 key。"""
    from app.core.crypto import mask_secret

    real_key = "sk-1234567890abcdef"
    masked = mask_secret(real_key)
    assert real_key not in masked
    assert masked.endswith("cdef")
    assert masked.startswith("sk-")


@pytest.mark.asyncio
async def test_create_provider_seals_api_key(monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.contexts.settings.models import LlmProvider
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.schemas import LlmProviderCreateRequest
    from app.contexts.settings.service import SettingsService
    from app.core.crypto import reveal_secret

    async def _ok(url: str) -> str:
        return url

    monkeypatch.setattr("app.contexts.settings.service.validate_public_https_url", _ok)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(LlmProvider.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        svc = SettingsService(SettingsRepository(session))
        created = await svc.create_provider(
            LlmProviderCreateRequest(
                name="DeepSeek",
                provider_type="deepseek",
                base_url="https://api.deepseek.com/anthropic",
                api_key="sk-test-key",
                model="deepseek-v4-flash",
            )
        )
        row = await session.get(LlmProvider, created.id)
        assert row is not None
        assert row.api_key_encrypted != "sk-test-key"
        assert reveal_secret(row.api_key_encrypted) == "sk-test-key"
        assert created.api_key_masked != "sk-test-key"
    await engine.dispose()
