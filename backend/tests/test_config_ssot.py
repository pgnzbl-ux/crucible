"""配置真相源：连接串只进 .env；产品版本只进 pyproject.toml；LLM 只走后台 Provider；MinIO bucket 写死。"""
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


def test_default_env_file_is_backend_local():
    """配置文件位置不应随 API/Celery 的启动目录变化。"""
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]
    assert Path(Settings.model_config["env_file"]).resolve() == backend_root / ".env"


def test_env_example_only_contains_supported_settings():
    """模板中的陈旧变量会被 Pydantic 静默忽略，必须在测试阶段直接报出。"""
    from pathlib import Path

    env_example = Path(__file__).resolve().parents[1] / ".env.example"
    declared = {
        line.split("=", 1)[0].strip()
        for line in env_example.read_text(encoding="utf-8").splitlines()
        if line and not line.lstrip().startswith("#") and "=" in line
    }
    supported = {name.upper() for name in Settings.model_fields}
    assert declared <= supported, f".env.example 存在无效配置: {sorted(declared - supported)}"


def test_infra_connection_fields_have_no_code_defaults():
    """连接串只进 .env，禁止在 Settings 里再抄一份 URL。"""
    for name in (
        "database_url",
        "redis_url",
        "celery_broker_url",
        "celery_result_backend",
        "s3_endpoint",
        "s3_access_key",
        "s3_secret_key",
    ):
        assert Settings.model_fields[name].is_required(), name


def test_pytest_uses_sqlite_not_runtime_postgres():
    """运行时 .env 是 PostgreSQL；pytest 进程必须覆盖成 sqlite，禁止打真实库。"""
    from app.core.config import get_settings

    url = get_settings().database_url
    assert url.startswith("sqlite"), url
    assert "postgresql" not in url


def test_app_version_has_single_source():
    """产品版本只写在 backend/pyproject.toml，禁止在 Settings / 前端再抄一份。"""
    import json
    import tomllib
    from pathlib import Path

    from app.core.config import get_settings

    backend_root = Path(__file__).resolve().parents[1]
    repo_root = backend_root.parent
    pyproject = tomllib.loads((backend_root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["version"]

    config_src = (backend_root / "app" / "core" / "config.py").read_text(encoding="utf-8")
    assert 'app_version: str =' not in config_src
    assert declared not in config_src

    assert "app_version" not in Settings.model_fields
    assert get_settings().app_version == declared

    frontend = json.loads((repo_root / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert "version" not in frontend


def test_settings_require_env_for_infra_urls(monkeypatch, tmp_path):
    from pydantic import ValidationError

    for key in (
        "DATABASE_URL",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "S3_ENDPOINT",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("")
    with pytest.raises(ValidationError):
        Settings(_env_file=empty)


_INFRA_ENV = {
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "REDIS_URL": "redis://localhost:6380/0",
    "CELERY_BROKER_URL": "redis://localhost:6380/1",
    "CELERY_RESULT_BACKEND": "redis://localhost:6380/2",
    "S3_ENDPOINT": "http://localhost:9000",
    "S3_ACCESS_KEY": "minioadmin",
    "S3_SECRET_KEY": "minioadmin",
}


@pytest.mark.parametrize("limit", ["0", "9"])
def test_concurrency_limit_rejects_out_of_range(monkeypatch, tmp_path, limit):
    from pydantic import ValidationError

    for key, value in _INFRA_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AGENT_RUNNER_CONCURRENCY_LIMIT", limit)
    empty = tmp_path / "empty.env"
    empty.write_text("")
    with pytest.raises(ValidationError):
        Settings(_env_file=empty)


def test_settings_has_no_s3_bucket_field():
    assert "s3_bucket" not in Settings.model_fields


def test_storage_buckets_are_platform_constants():
    from app.shared.object_store import KIND_REGISTRY, PHYSICAL_BUCKETS

    assert PHYSICAL_BUCKETS == ("crucible-durable", "crucible-task", "crucible-public")
    assert KIND_REGISTRY["source"].bucket == "crucible-durable"
    assert KIND_REGISTRY["evidence"].bucket == "crucible-task"
    assert KIND_REGISTRY["report"].bucket == "crucible-task"


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
    assert env["HOME"] != "/workspace"
    assert env["HOME"].startswith("/tmp")


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
    with patch(
        "app.contexts.agent.preflight.agent_runner_manager.image_exists",
        return_value=True,
    ):
        with patch(
            "app.contexts.settings.service.SettingsService.get_default_provider",
            new_callable=AsyncMock,
            return_value=None,
        ):
            ok, msg = await _platform_preflight_minimal(session)
    assert ok is False
    assert msg is not None
    assert "LLM Provider" in msg
    assert "llm_api_key" not in msg
    assert "LLM_API_KEY" not in msg


@pytest.mark.asyncio
async def test_preflight_fails_on_empty_api_key():
    from types import SimpleNamespace

    from app.contexts.agent.tasks import _platform_preflight_minimal

    session = MagicMock()
    empty = SimpleNamespace(api_key_encrypted="  ")
    with patch(
        "app.contexts.agent.preflight.agent_runner_manager.image_exists",
        return_value=True,
    ):
        with patch(
            "app.contexts.settings.service.SettingsService.get_default_provider",
            new_callable=AsyncMock,
            return_value=empty,
        ):
            ok, msg = await _platform_preflight_minimal(session)
    assert ok is False
    assert msg is not None
    assert "API Key" in msg


@pytest.mark.asyncio
async def test_preflight_fails_without_runner_image():
    from types import SimpleNamespace

    from app.contexts.agent.tasks import _platform_preflight_minimal

    session = MagicMock()
    ready = SimpleNamespace(api_key_encrypted="sk-test")
    with patch(
        "app.contexts.agent.preflight.agent_runner_manager.image_exists",
        return_value=False,
    ):
        with patch(
            "app.contexts.settings.service.SettingsService.get_default_provider",
            new_callable=AsyncMock,
            return_value=ready,
        ):
            ok, msg = await _platform_preflight_minimal(session)
    assert ok is False
    assert msg is not None
    assert "agent-runner 镜像" in msg
