"""Settings LLM Provider API 必须要求登录。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.contexts.settings.api import get_settings_service, router


class _FakeSettingsService:
    async def list_providers(self):
        return [], 0


def test_llm_provider_list_requires_authentication(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "environment", "production")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_settings_service] = lambda: _FakeSettingsService()

    response = TestClient(app).get("/api/v1/settings/llm/providers")

    assert response.status_code == 401
