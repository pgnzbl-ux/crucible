"""Provider 认证、effort 与 Agent env 的运行时单一契约。"""

from types import SimpleNamespace

import httpx
import pytest

from app.contexts.settings.provider_runtime import (
    ProviderRuntimeConfig,
    anthropic_auth_headers,
    default_auth_mode,
    normalize_effort,
    resolve_auth_mode,
)


def _provider(**overrides):
    values = {
        "id": "p1",
        "provider_type": "custom",
        "auth_mode": "bearer",
        "base_url": "https://gateway.example",
        "api_key_encrypted": "secret",
        "model": "model-1",
        "timeout_ms": 60_000,
        "temperature": 0.2,
        "max_context_tokens": 64_000,
        "effort": "high",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_provider_type_defaults_preserve_legacy_and_anthropic_semantics():
    assert default_auth_mode("anthropic") == "api_key"
    assert default_auth_mode("deepseek") == "bearer"
    assert default_auth_mode("custom") == "bearer"
    assert resolve_auth_mode("custom", "api_key") == "api_key"


def test_auth_headers_never_mix_schemes():
    assert anthropic_auth_headers("key", "api_key") == {"x-api-key": "key"}
    assert anthropic_auth_headers("token", "bearer") == {"authorization": "Bearer token"}


def test_agent_env_contains_exactly_one_auth_variable():
    bearer = ProviderRuntimeConfig.from_provider(_provider()).agent_env()
    api_key = ProviderRuntimeConfig.from_provider(_provider(provider_type="anthropic", auth_mode="api_key")).agent_env()

    assert bearer["ANTHROPIC_AUTH_TOKEN"] == "secret"
    assert "ANTHROPIC_API_KEY" not in bearer
    assert api_key["ANTHROPIC_API_KEY"] == "secret"
    assert "ANTHROPIC_AUTH_TOKEN" not in api_key
    assert bearer["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "0"
    assert api_key["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "0"


def test_auto_effort_is_not_sent_to_agent_environment():
    env = ProviderRuntimeConfig.from_provider(_provider(effort="auto")).agent_env()

    assert normalize_effort("auto") is None
    assert "CLAUDE_CODE_EFFORT_LEVEL" not in env
    assert "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT" not in env


def test_invalid_auth_mode_is_rejected():
    with pytest.raises(ValueError, match="认证方式"):
        resolve_auth_mode("custom", "basic")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_mode", "expected_header", "unexpected_header"),
    [
        ("api_key", "x-api-key", "authorization"),
        ("bearer", "authorization", "x-api-key"),
    ],
)
async def test_connection_uses_one_auth_scheme_and_omits_auto_effort(
    monkeypatch,
    auth_mode,
    expected_header,
    unexpected_header,
):
    from app.contexts.settings.service import SettingsService

    captured: dict = {}

    async def allow_url(url: str) -> str:
        return url

    class Response:
        status_code = 200
        text = ""

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return Response()

    monkeypatch.setattr("app.contexts.settings.service.validate_public_https_url", allow_url)
    monkeypatch.setattr(httpx, "AsyncClient", Client)

    svc = SettingsService(SimpleNamespace())
    result = await svc.test_connection(
        base_url="https://gateway.example",
        provider_type="custom",
        auth_mode=auth_mode,
        api_key="secret",
        model="model-1",
        effort="auto",
    )

    assert result.ok is True
    assert expected_header in captured["headers"]
    assert unexpected_header not in captured["headers"]
    assert "output_config" not in captured["json"]
