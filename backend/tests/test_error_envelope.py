"""API 错误必须同时给契约信封 error 与兼容字段 detail。"""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.contexts.task.api import get_task_service, router
from app.shared.deps import get_current_user_id
from app.shared.exception_handlers import register_exception_handlers
from app.shared.exceptions import ConflictError, NotFoundError


def _app_with_handlers() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    return app


def test_value_error_uses_error_envelope():
    app = _app_with_handlers()

    @app.get("/boom")
    def boom():
        raise ValueError("Git 地址非法")

    response = TestClient(app).get("/boom")
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "BAD_REQUEST"
    assert body["error"]["message"] == "Git 地址非法"
    assert body["detail"] == "Git 地址非法"


def test_http_exception_keeps_lab_in_use_detail_and_adds_envelope():
    app = _app_with_handlers()

    @app.post("/labs/busy")
    def busy():
        raise HTTPException(
            409,
            detail={"code": "LAB_IN_USE", "message": "靶场正被任务使用", "task_ids": ["t1"]},
        )

    response = TestClient(app).post("/labs/busy")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "LAB_IN_USE"
    assert body["error"]["message"] == "靶场正被任务使用"
    assert body["error"]["details"]["task_ids"] == ["t1"]
    assert body["detail"]["code"] == "LAB_IN_USE"
    assert body["detail"]["task_ids"] == ["t1"]


def test_retry_missing_task_returns_404_envelope():
    class _FakeTaskService:
        async def retry_task(self, task_id, owner_id, from_node=None):
            raise NotFoundError(
                "任务不存在",
                code="TASK_NOT_FOUND",
                details={"task_id": task_id},
            )

    app = _app_with_handlers()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_task_service] = lambda: _FakeTaskService()
    app.dependency_overrides[get_current_user_id] = lambda: "u1"

    response = TestClient(app).post("/api/v1/tasks/missing/retry")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "TASK_NOT_FOUND"
    assert body["error"]["message"] == "任务不存在"
    assert body["detail"] == "任务不存在"


def test_conflict_error_uses_error_envelope():
    app = _app_with_handlers()

    @app.post("/projects")
    def dup():
        raise ConflictError("项目名称已存在: demo，请换一个名称")

    response = TestClient(app).post("/projects")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "CONFLICT"
    assert "项目名称已存在" in body["error"]["message"]
    assert body["detail"] == body["error"]["message"]
