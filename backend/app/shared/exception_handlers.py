"""统一错误信封：{error:{code,message,details}}，同时保留 detail 兼容旧前端。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.shared.exceptions import CrucibleError

logger = logging.getLogger(__name__)

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


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """pydantic v2 的 ctx 可能携带原始 ValueError 对象(model_validator 抛出)，
    JSONResponse 无法序列化会炸掉整个 422 —— 统一降级为字符串。"""
    cleaned: list[dict[str, Any]] = []
    for err in errors:
        row = dict(err)
        ctx = row.get("ctx")
        if isinstance(ctx, dict):
            row["ctx"] = {k: (str(v) if isinstance(v, BaseException) else v) for k, v in ctx.items()}
        cleaned.append(row)
    return cleaned


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
                details={"errors": _sanitize_validation_errors(exc.errors())},
                detail="请求校验失败",
            ),
        )

    @app.exception_handler(ValidationError)
    async def pydantic_validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        """pydantic v2 的 ValidationError 是 ValueError 子类：端点内构造响应
        模型失败属于服务端 bug(如 DB 脏行 vs schema 契约)，不能落进下面的
        ValueError→400 被伪装成客户端错误。Starlette 按 MRO 取最具体 handler，
        此处优先命中 → 500 + 堆栈进日志。请求体校验仍走 RequestValidationError→422。"""
        logger.exception("响应模型校验失败 %s %s", request.method, request.url.path)
        from app.core.config import get_settings

        settings = get_settings()
        message = (
            f"{type(exc).__name__}: {exc}" if settings.environment == "development" else "服务器内部错误"
        )
        return JSONResponse(
            status_code=500,
            content=error_body(code="INTERNAL_ERROR", message=message),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=error_body(code="BAD_REQUEST", message=str(exc)),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """兜底：未捕获异常也要回统一信封（前端读 error.message），并保证堆栈进服务端日志。"""
        from app.core.config import get_settings

        logger.exception("未处理异常 %s %s", request.method, request.url.path)
        settings = get_settings()
        message = (
            f"{type(exc).__name__}: {exc}"
            if settings.environment == "development"
            else "服务器内部错误"
        )
        return JSONResponse(
            status_code=500,
            content=error_body(code="INTERNAL_ERROR", message=message),
        )
