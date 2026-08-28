"""全链路请求上下文 (借鉴 OpenStack oslo_context 模式)。

在 HTTP 请求、Celery 异步 Worker、DAG 编排器与沙箱容器之间无缝传递统一的上下文：
- request_id: 分布式追踪 ID（贯穿日志与容器 Label）
- user_id: 操作人 ID
- role / is_admin: 权限角色
- project_id / task_id: 租户与任务范围
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

_HEADER_REQUEST_ID = "x-request-id"
_HEADER_USER_ID = "x-user-id"
_HEADER_ROLE = "x-user-role"
_HEADER_IS_ADMIN = "x-is-admin"
_HEADER_PROJECT_ID = "x-project-id"
_HEADER_TASK_ID = "x-task-id"


@dataclass
class CrucibleContext:
    """标准全链路上下文。"""
    request_id: str = field(default_factory=lambda: uuid4().hex)
    user_id: str = ""
    role: str = "viewer"
    is_admin: bool = False
    project_id: str | None = None
    task_id: str | None = None
    auth_token: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化供 Celery Task Headers / RPC Payload 传输。"""
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "role": self.role,
            "is_admin": self.is_admin,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CrucibleContext":
        if not data:
            return cls()
        return cls(
            request_id=data.get("request_id") or uuid4().hex,
            user_id=data.get("user_id") or "",
            role=data.get("role") or "viewer",
            is_admin=bool(data.get("is_admin", False)),
            project_id=data.get("project_id"),
            task_id=data.get("task_id"),
            extra=data.get("extra") or {},
        )

    def to_headers(self) -> dict[str, str]:
        """转为 HTTP / gRPC 元数据 Header。"""
        headers = {
            _HEADER_REQUEST_ID: self.request_id,
            _HEADER_USER_ID: self.user_id,
            _HEADER_ROLE: self.role,
            _HEADER_IS_ADMIN: "1" if self.is_admin else "0",
        }
        if self.project_id:
            headers[_HEADER_PROJECT_ID] = str(self.project_id)
        if self.task_id:
            headers[_HEADER_TASK_ID] = str(self.task_id)
        return headers

    @classmethod
    def from_headers(cls, headers: Any) -> "CrucibleContext":
        """从 HTTP Headers 解析上下文。"""
        def _get(key: str) -> str | None:
            if hasattr(headers, "get"):
                return headers.get(key)
            return None

        req_id = _get(_HEADER_REQUEST_ID) or _get("X-Request-ID") or uuid4().hex
        user_id = _get(_HEADER_USER_ID) or _get("X-User-ID") or ""
        role = _get(_HEADER_ROLE) or _get("X-User-Role") or "viewer"
        is_admin_str = _get(_HEADER_IS_ADMIN) or _get("X-Is-Admin") or "0"
        project_id = _get(_HEADER_PROJECT_ID) or _get("X-Project-ID")
        task_id = _get(_HEADER_TASK_ID) or _get("X-Task-ID")

        return cls(
            request_id=req_id,
            user_id=user_id,
            role=role,
            is_admin=is_admin_str in ("1", "true", "True"),
            project_id=project_id,
            task_id=task_id,
        )


_context_var: contextvars.ContextVar[CrucibleContext | None] = contextvars.ContextVar(
    "crucible_context", default=None
)


def get_current_context() -> CrucibleContext:
    """获取当前协程/线程内的上下文（若无则创建默认空上下文）。"""
    ctx = _context_var.get()
    if ctx is None:
        ctx = CrucibleContext()
        _context_var.set(ctx)
    return ctx


def set_current_context(ctx: CrucibleContext) -> contextvars.Token:
    """显式设置当前上下文。"""
    return _context_var.set(ctx)


def reset_current_context(token: contextvars.Token) -> None:
    """恢复上下文。"""
    _context_var.reset(token)
