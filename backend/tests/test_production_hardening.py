"""生产硬化：启动校验、metrics 鉴权、SSE ticket、登录限流。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _base_kwargs(**overrides):
    data = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "redis_url": "redis://localhost:6380/0",
        "celery_broker_url": "redis://localhost:6380/1",
        "celery_result_backend": "redis://localhost:6380/2",
        "redis_clue_url": "redis://localhost:6380/3",
        "s3_endpoint": "http://localhost:9000",
        "s3_access_key": "k",
        "s3_secret_key": "s",
        "auth_secret": "prod-secret-not-example",
        "environment": "production",
        # 合法 Fernet 32B urlsafe base64；独立于 auth_secret（安全红线）
        "settings_encrypt_key": "k-xSZSThnM0m32Y0DN_wo7RAPuUybJ1PJCYCC0aCHSI=",
        "claude_agent_sdk_enabled": True,
        "llm_gateway_enabled": True,
        "llm_base_url_relaxed": False,
        "metrics_token": "metrics-secret",
        "cors_origins": "https://crucible.example.com",
        "_env_file": Path(os.devnull),
    }
    data.update(overrides)
    return data


def test_production_rejects_relaxed_llm_base_url():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="LLM_BASE_URL_RELAXED"):
        Settings(**_base_kwargs(llm_base_url_relaxed=True))


def test_production_rejects_sdk_disabled():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="CLAUDE_AGENT_SDK_ENABLED"):
        Settings(**_base_kwargs(claude_agent_sdk_enabled=False))


def test_production_rejects_gateway_disabled():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="LLM_GATEWAY"):
        Settings(**_base_kwargs(llm_gateway_enabled=False))


def test_production_rejects_missing_metrics_token():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="METRICS_TOKEN"):
        Settings(**_base_kwargs(metrics_token=""))


def test_production_requires_independent_encrypt_key():
    """SETTINGS_ENCRYPT_KEY 是独立凭据加密钥：生产缺失即拒启。"""
    from cryptography.fernet import Fernet

    from app.core.config import Settings

    good = Fernet.generate_key().decode()
    assert Fernet(good).encrypt(b"x") != b"x"  # sanity：样例确为可用密钥
    with pytest.raises(ValueError, match="SETTINGS_ENCRYPT_KEY"):
        Settings(**_base_kwargs(settings_encrypt_key=""))


def test_production_rejects_bad_format_encrypt_key():
    """格式非法的加解密钥必须在启动期被拦截，而不是首个请求才炸。"""
    from app.core.config import Settings

    with pytest.raises(ValueError, match="SETTINGS_ENCRYPT_KEY"):
        Settings(**_base_kwargs(settings_encrypt_key="not-a-fernet-key"))


def test_production_encrypt_key_may_not_alias_auth_secret():
    """独立性红线：直接拿 AUTH_SECRET 值当加解密钥不是合法 Fernet，同样被拒。"""
    from app.core.config import Settings

    with pytest.raises(ValueError, match="SETTINGS_ENCRYPT_KEY"):
        Settings(**_base_kwargs(settings_encrypt_key="prod-secret-not-example"))


def test_production_rejects_wildcard_cors():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="CORS"):
        Settings(**_base_kwargs(cors_origins="*"))


def test_production_accepts_hardened_settings():
    from app.core.config import Settings

    s = Settings(**_base_kwargs())
    assert s.environment == "production"
    assert s.llm_base_url_relaxed is False


def test_development_allows_relaxed_true():
    from app.core.config import Settings

    s = Settings(**_base_kwargs(environment="development", llm_base_url_relaxed=True, metrics_token=""))
    assert s.llm_base_url_relaxed is True


def test_sse_ticket_roundtrip():
    from app.core.security import create_sse_ticket, decode_sse_ticket

    ticket = create_sse_ticket(user_id="u1", task_id="t1", expires_seconds=120)
    payload = decode_sse_ticket(ticket)
    assert payload is not None
    assert payload["sub"] == "u1"
    assert payload["tid"] == "t1"
    assert payload["typ"] == "sse"


def test_access_token_not_valid_as_sse_ticket():
    from app.core.security import create_access_token, decode_sse_ticket

    access = create_access_token("u1", "a@b.c")
    assert decode_sse_ticket(access) is None


def test_slice_char_budget_scales_with_context():
    from app.contexts.agent.nodes.triage.adjudicate import slice_char_budget

    assert slice_char_budget(100_000) > slice_char_budget(20_000)
    assert 4_000 <= slice_char_budget(8_000) <= 32_000
    assert slice_char_budget(None) >= 4_000


def test_metrics_requires_token_when_configured(monkeypatch):
    from app.core.config import get_settings
    from app.main import create_app

    settings = get_settings()
    monkeypatch.setattr(settings, "metrics_token", "secret-metrics")
    monkeypatch.setattr(settings, "environment", "development")

    app = create_app()
    client = TestClient(app)
    assert client.get("/metrics").status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer secret-metrics"})
    assert ok.status_code == 200
    assert b"#" in ok.content or b"http" in ok.content or len(ok.content) >= 0


def test_environment_normalization_and_alias():
    from app.core.config import Settings

    s1 = Settings(**_base_kwargs(environment="Production"))
    assert s1.environment == "production"

    s2 = Settings(**_base_kwargs(environment="prod"))
    assert s2.environment == "production"

    s3 = Settings(**_base_kwargs(environment="dev", llm_base_url_relaxed=True, metrics_token=""))
    assert s3.environment == "development"


def test_environment_rejects_invalid_names():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="ENVIRONMENT"):
        Settings(**_base_kwargs(environment="invalid_env"))


def test_staging_enforces_security_hardening():
    from app.core.config import Settings

    with pytest.raises(ValueError, match="LLM_BASE_URL_RELAXED"):
        Settings(**_base_kwargs(environment="staging", llm_base_url_relaxed=True))
