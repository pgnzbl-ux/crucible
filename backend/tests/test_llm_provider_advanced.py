"""LLM Provider 高级设置：全局默认与校验。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.contexts.settings.models import (
    DEFAULT_LLM_EFFORT,
    DEFAULT_LLM_MAX_CONTEXT_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
    LlmProvider,
)


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


def test_create_schema_defaults_advanced_fields():
    from app.contexts.settings.schemas import LlmProviderCreateRequest

    req = LlmProviderCreateRequest(
        name="DeepSeek",
        provider_type="deepseek",
        base_url="https://api.deepseek.com/anthropic",
        api_key="sk-test",
        model="deepseek-v4-flash",
    )
    assert req.temperature == DEFAULT_LLM_TEMPERATURE
    assert req.max_context_tokens == DEFAULT_LLM_MAX_CONTEXT_TOKENS
    assert req.effort == DEFAULT_LLM_EFFORT
    assert req.auth_mode is None


def test_create_rejects_invalid_effort_and_temperature():
    from app.contexts.settings.schemas import LlmProviderCreateRequest

    with pytest.raises(ValidationError):
        LlmProviderCreateRequest(
            name="bad",
            provider_type="deepseek",
            base_url="https://api.deepseek.com/anthropic",
            api_key="sk-test",
            model="deepseek-v4-flash",
            effort="ultra",
        )
    with pytest.raises(ValidationError):
        LlmProviderCreateRequest(
            name="bad",
            provider_type="deepseek",
            base_url="https://api.deepseek.com/anthropic",
            api_key="sk-test",
            model="deepseek-v4-flash",
            temperature=3.0,
        )


@pytest.mark.asyncio
async def test_create_provider_persists_advanced_defaults(session, skip_url_check):
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.schemas import LlmProviderCreateRequest
    from app.contexts.settings.service import SettingsService

    svc = SettingsService(SettingsRepository(session))
    created = await svc.create_provider(
        LlmProviderCreateRequest(
            name="A",
            provider_type="deepseek",
            base_url="https://api.deepseek.com/anthropic",
            api_key="sk-test",
            model="deepseek-v4-flash",
        )
    )
    assert created.temperature == DEFAULT_LLM_TEMPERATURE
    assert created.max_context_tokens == DEFAULT_LLM_MAX_CONTEXT_TOKENS
    assert created.effort == DEFAULT_LLM_EFFORT
    assert created.auth_mode == "bearer"

    env = svc.build_env_from_provider(await svc.get_default_provider())  # type: ignore[arg-type]
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == str(DEFAULT_LLM_MAX_CONTEXT_TOKENS)
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == DEFAULT_LLM_EFFORT


@pytest.mark.asyncio
async def test_llm_complete_uses_provider_temperature(monkeypatch):
    """Messages 路径只读 Provider.temperature，无调用方硬编码。"""
    import httpx

    from app.contexts.settings.models import LlmProvider
    from app.core import llm_gateway

    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "model": "deepseek-v4-flash",
                "content": [{"type": "text", "text": '{"verdict":"fp"}'}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: type("S", (), {"llm_gateway_enabled": True})(),
    )
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    provider = LlmProvider(
        id="p1",
        name="t",
        provider_type="deepseek",
        base_url="https://api.deepseek.com/anthropic",
        api_key_encrypted="sk-x",
        model="deepseek-v4-flash",
        timeout_ms=60000,
        temperature=0.2,
        max_context_tokens=200000,
        effort="high",
        is_default=True,
    )
    result = await llm_gateway.llm_complete(
        role="screening",
        system="s",
        user="u",
        provider=provider,
        max_tokens=16,
    )
    assert result.text
    assert captured["json"]["temperature"] == 0.2
    assert captured["json"]["output_config"]["effort"] == "high"
    assert captured["headers"]["authorization"] == "Bearer sk-x"
    assert "x-api-key" not in captured["headers"]


@pytest.mark.asyncio
async def test_llm_complete_honors_timeout_and_retries_5xx(monkeypatch):
    """Provider.timeout_ms 必须生效，5xx 必须按约定重试。"""
    import httpx

    from app.core import llm_gateway

    calls: list[float] = []

    class _Client:
        def __init__(self, *, timeout):
            calls.append(timeout)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            request = httpx.Request("POST", url)
            if len(calls) < 3:
                return httpx.Response(503, text="busy", request=request)
            return httpx.Response(
                200,
                json={
                    "model": "model-1",
                    "content": [{"type": "text", "text": "ok"}],
                    "usage": {},
                },
                request=request,
            )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: type("S", (), {"llm_gateway_enabled": True})(),
    )
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(llm_gateway.asyncio, "sleep", no_sleep)

    result = await llm_gateway.llm_complete(
        role="screening",
        system="s",
        user="u",
        provider=type(
            "Provider",
            (),
            {
                "id": "p1",
                "provider_type": "custom",
                "auth_mode": "bearer",
                "base_url": "https://gateway.example",
                "api_key_encrypted": "secret",
                "model": "model-1",
                "timeout_ms": 600_000,
                "temperature": 0.2,
                "max_context_tokens": 200_000,
                "effort": "auto",
            },
        )(),
    )

    assert result.text == "ok"
    assert calls == [600.0, 600.0, 600.0]


@pytest.mark.asyncio
async def test_llm_complete_does_not_silently_drop_explicit_effort(monkeypatch):
    """显式 effort 不受支持时应报错，不能悄悄降级为无 effort 请求。"""
    import httpx

    from app.core import llm_gateway

    payloads: list[dict] = []

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            payloads.append(json)
            return httpx.Response(
                422,
                text="output_config unsupported",
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: type("S", (), {"llm_gateway_enabled": True})(),
    )
    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    provider = type(
        "Provider",
        (),
        {
            "id": "p1",
            "provider_type": "custom",
            "auth_mode": "bearer",
            "base_url": "https://gateway.example",
            "api_key_encrypted": "secret",
            "model": "model-1",
            "timeout_ms": 60_000,
            "temperature": 0.2,
            "max_context_tokens": 200_000,
            "effort": "high",
        },
    )()

    with pytest.raises(llm_gateway.LlmGatewayConfigError, match="HTTP 422"):
        await llm_gateway.llm_complete(
            role="screening", system="s", user="u", provider=provider
        )

    assert len(payloads) == 1
    assert payloads[0]["output_config"] == {"effort": "high"}
