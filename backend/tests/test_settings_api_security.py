"""Settings LLM Provider API 必须要求登录。"""
import pytest
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "is_admin,role,allowed",
    [
        (True, "admin", True),
        (False, "admin", True),
        (False, "analyst", False),
        (False, "viewer", False),
    ],
)
async def test_platform_settings_require_admin(is_admin, role, allowed):
    from fastapi import HTTPException

    from app.shared.deps import get_current_admin_id

    class _Session:
        async def get(self, _model, _user_id):
            return type("User", (), {"is_active": True, "is_admin": is_admin, "role": role})()

    if allowed:
        assert await get_current_admin_id("u1", _Session()) == "u1"
    else:
        with pytest.raises(HTTPException) as exc:
            await get_current_admin_id("u1", _Session())
        assert exc.value.status_code == 403
