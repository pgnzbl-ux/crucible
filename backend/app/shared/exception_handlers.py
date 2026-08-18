"""统一错误信封：{error:{code,message,details}}，同时保留 detail 兼容旧前端。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.shared.exceptions import CrucibleError

_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_FAILED",
    428: "PRECONDITION_REQUIRED",
    503: "SERVICE_UNAVAILABLE",
}


def error_body(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    detail: Any = None,
) -> dict[str, Any]:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "detail": message if detail is None else detail,
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(CrucibleError)
    async def crucible_error_handler(_request: Request, exc: CrucibleError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content=error_body(code=exc.code, message=exc.message, details=exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            code = str(detail.get("code") or _STATUS_CODES.get(exc.status_code, "HTTP_ERROR"))
            message = str(detail.get("message") or "")
            extra = {k: v for k, v in detail.items() if k not in ("code", "message")}
            body = error_body(code=code, message=message or code, details=extra, detail=detail)
        else:
            message = str(detail)
            body = error_body(
                code=_STATUS_CODES.get(exc.status_code, "HTTP_ERROR"),
                message=message,
            )
        headers = getattr(exc, "headers", None)
        if headers:
            return JSONResponse(status_code=exc.status_code, content=body, headers=headers)
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                code="VALIDATION_FAILED",
                message="请求校验失败",
                details={"errors": exc.errors()},
                detail="请求校验失败",
            ),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=error_body(code="BAD_REQUEST", message=str(exc)),
        )
