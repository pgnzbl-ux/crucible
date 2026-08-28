"""架构重构与解耦核心组件测试：
- CrucibleContext 上下文传递与 Header 解析
- PolicyEngine 声明式权限策略
- ServiceCatalog 统一服务目录
- BaseRunnerDriver 驱动抽象契约
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.catalog import ServiceCatalog, ServiceType, catalog
from app.core.runner_driver import BaseRunnerDriver
from app.core.agent_runner import AgentRunnerManager
from app.shared.context import (
    CrucibleContext,
    get_current_context,
    reset_current_context,
    set_current_context,
)
from app.shared.policy import PolicyEngine, check_policy, enforce_policy


def test_crucible_context_lifecycle_and_headers():
    ctx = CrucibleContext(
        user_id="usr_123",
        role="analyst",
        is_admin=False,
        project_id="prj_456",
        task_id="tsk_789",
    )
    headers = ctx.to_headers()
    assert headers["x-user-id"] == "usr_123"
    assert headers["x-user-role"] == "analyst"
    assert headers["x-is-admin"] == "0"
    assert headers["x-project-id"] == "prj_456"
    assert headers["x-task-id"] == "tsk_789"

    # 从 Headers 还原
    restored = CrucibleContext.from_headers(headers)
    assert restored.user_id == "usr_123"
    assert restored.role == "analyst"
    assert not restored.is_admin
    assert restored.project_id == "prj_456"
    assert restored.task_id == "tsk_789"

    # ContextVars 隔离
    token = set_current_context(ctx)
    assert get_current_context().user_id == "usr_123"
    reset_current_context(token)


def test_policy_engine_role_authorization():
    admin_ctx = CrucibleContext(user_id="admin_1", role="admin", is_admin=True)
    analyst_ctx = CrucibleContext(user_id="analyst_1", role="analyst", is_admin=False)
    viewer_ctx = CrucibleContext(user_id="viewer_1", role="viewer", is_admin=False)

    # 1. 任务创建
    assert check_policy("task:create", admin_ctx)
    assert check_policy("task:create", analyst_ctx)
    assert not check_policy("task:create", viewer_ctx)

    # 2. 系统设置修改
    assert check_policy("settings:modify", admin_ctx)
    assert not check_policy("settings:modify", analyst_ctx)
    assert not check_policy("settings:modify", viewer_ctx)

    # 3. 强制执行拒绝抛出 403
    with pytest.raises(HTTPException) as exc_info:
        enforce_policy("task:create", viewer_ctx)
    assert exc_info.value.status_code == 403


def test_service_catalog_registration_and_query():
    cat = ServiceCatalog()
    scanners = cat.list_by_type(ServiceType.SCANNER)
    assert any(s.name == "scanner-semgrep" for s in scanners)
    assert any(s.name == "scanner-gitleaks" for s in scanners)

    runner = cat.get("agent-runner-docker")
    assert runner is not None
    assert runner.protocol == "docker"


def test_agent_runner_manager_implements_runner_driver():
    manager = AgentRunnerManager()
    assert isinstance(manager, BaseRunnerDriver)
