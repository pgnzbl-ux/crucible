"""开发模式匿名回退的边界：仅限本机来源，局域网匿名请求必须 401。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.deps import CurrentUserId


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(user_id: CurrentUserId):
        return {"user_id": user_id}

    return app


def test_dev_fallback_allows_testclient_source():
    """TestClient 固定来源 testclient —— 单测/冒烟的 system 回退不受影响。"""
    resp = TestClient(_app()).get("/whoami")
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "system"


def test_dev_fallback_rejects_lan_source():
    """0.0.0.0 监听下，局域网匿名请求不得以 system 身份通过。"""
    resp = TestClient(_app(), client=("192.168.1.23", 51234)).get("/whoami")
    assert resp.status_code == 401
