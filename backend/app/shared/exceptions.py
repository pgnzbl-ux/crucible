"""领域异常 — 不继承 HTTPException，由 exception_handlers 转成统一信封。"""

from __future__ import annotations

from typing import Any


class CrucibleError(Exception):
    """业务异常基类。code + message + http_status，API 层统一包成 error 信封。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "BAD_REQUEST",
        http_status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.http_status = http_status
        self.details = details or {}


class NotFoundError(CrucibleError):
    def __init__(
        self,
        message: str = "资源不存在",
        *,
        code: str = "NOT_FOUND",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, http_status=404, details=details)


class ConflictError(CrucibleError):
    def __init__(
        self,
        message: str = "资源冲突",
        *,
        code: str = "CONFLICT",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code, http_status=409, details=details)
