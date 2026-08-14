"""配置真相源：LLM 只走后台 Provider；Redis 默认 6380；MinIO bucket 写死。"""
import sys
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.core.config import Settings


_LLM_ENV_FIELDS = (
    "llm_base_url",
    "llm_api_key",
    "llm_model",
    "llm_timeout_ms",
    "llm_disable_nonessential_traffic",
)


def test_settings_has_no_llm_env_fields():
    for name in _LLM_ENV_FIELDS:
        assert name not in Settings.model_fields, f"{name} 不应再从 .env 注入"


def test_settings_redis_defaults_use_host_mapped_ports():
    fields = Settings.model_fields
    assert fields["redis_url"].default == "redis://localhost:6380/0"
    assert fields["celery_broker_url"].default == "redis://localhost:6380/1"
    assert fields["celery_result_backend"].default == "redis://localhost:6380/2"


def test_settings_has_no_s3_bucket_field():
    assert "s3_bucket" not in Settings.model_fields


def test_storage_buckets_are_platform_constants():
    from app.contexts.report.storage import ARTIFACTS_BUCKET, EVIDENCE_BUCKET
    from app.contexts.project.source_cache import SOURCE_BUCKET

    assert ARTIFACTS_BUCKET == "crucible-artifacts"
    assert EVIDENCE_BUCKET == "crucible-evidence"
    assert SOURCE_BUCKET == "crucible-source"


def test_build_runner_env_ignores_settings_llm(monkeypatch):
    monkeypatch.setattr(
        "app.contexts.agent.sdk_adapter.settings",
        SimpleNamespace(claude_sdk_max_turns=9),
    )
    from app.contexts.agent.sdk_adapter import ClaudeSdkAdapter

    env = ClaudeSdkAdapter().build_runner_env({})
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_BASE_URL" not in env
    assert env["CLAUDE_SDK_MAX_TURNS"] == "9"


def test_build_runner_env_uses_provider_env(monkeypatch):
    monkeypatch.setattr(
        "app.contexts.agent.sdk_adapter.settings",
        SimpleNamespace(claude_sdk_max_turns=180),
    )
    from app.contexts.agent.sdk_adapter import ClaudeSdkAdapter

    provider_env = {
        "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        "ANTHROPIC_AUTH_TOKEN": "sk-from-db",
        "ANTHROPIC_API_KEY": "sk-from-db",
        "ANTHROPIC_MODEL": "deepseek-v4-flash",
        "API_TIMEOUT_MS": "600000",
    }
    env = ClaudeSdkAdapter().build_runner_env(provider_env)
    assert env["ANTHROPIC_API_KEY"] == "sk-from-db"
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-flash"


def test_no_env_llm_seed_module():
    with pytest.raises(ImportError):
        import app.contexts.settings.seed  # noqa: F401


def test_llm_credential_hint_points_to_settings_only():
    from app.contexts.agent.errors import humanize_agent_error

    _, hint = humanize_agent_error("缺少 LLM 凭据：未配置默认 Provider")
    assert "设置" in hint
    assert ".env" not in hint
    assert "LLM_API_KEY" not in hint


@pytest.mark.asyncio
async def test_preflight_fails_without_default_provider():
    from app.contexts.agent.tasks import _platform_preflight_minimal

    session = MagicMock()
    with patch("app.contexts.agent.tasks.agent_runner_manager") as mgr:
        mgr.image_exists.return_value = True
        with patch(
            "app.contexts.settings.service.SettingsService.get_default_provider",
            new_callable=AsyncMock,
            return_value=None,
        ):
            ok, msg = await _platform_preflight_minimal(session)
    assert ok is False
    assert msg is not None
    assert "默认 Provider" in msg
    assert "llm_api_key" not in msg
    assert "LLM_API_KEY" not in msg
