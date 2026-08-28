"""
共享依赖 — 跨 Context 的 FastAPI Depends。

当前提供：
- get_current_user_id：从 Authorization Bearer 解析 access JWT → user_id
  - 开发模式本机可回退 system（见下）
- get_sse_user_id：SSE 专用 —— 优先 ?ticket=（短命），开发环境仍兼容 ?token= access JWT

设计：
- 鉴权逻辑集中在此，各 Context 只 Depends 即可
- 返回 user_id（str），不返回 User 对象
- 凭据缺失/失效统一抛 401
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token, decode_sse_ticket
from app.core.database import get_db_session
from app.shared.context import CrucibleContext, set_current_context

# auto_error=False：SSE 端点用 query，缺 header 不在此抛错
_bearer = HTTPBearer(auto_error=False)


def _dev_system_fallback(request: Request) -> str | None:
    from app.core.config import get_settings

    settings = get_settings()
    if settings.environment != "development":
        return None
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "testclient", ""):
        return "system"
    return None


async def _require_active_user(session: AsyncSession, user_id: str) -> str:
    from app.contexts.identity.models import User

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "用户不存在或已停用")
    return user_id


async def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> str:
    """解析当前登录用户 id（仅 Authorization Bearer access JWT）。

    开发模式未带 token 时，本机来源可回退 "system"（Mock 冒烟）。
    """
    if credentials and credentials.credentials:
        payload = decode_access_token(credentials.credentials)
        if payload and payload.get("sub"):
            return await _require_active_user(session, str(payload["sub"]))
        raise HTTPException(401, "凭据无效或已过期")

    fallback = _dev_system_fallback(request)
    if fallback is not None:
        return fallback
    raise HTTPException(401, "未提供认证凭据")


async def get_sse_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    ticket: Annotated[
        str | None,
        Query(description="短命 SSE ticket（POST .../events/ticket 领取）"),
    ] = None,
    token_query: Annotated[
        str | None,
        Query(
            alias="token",
            description="[开发兼容] access JWT；生产请改用 ticket",
        ),
    ] = None,
    task_id: str = "",  # 由路径注入时需在端点里校验 ticket.tid
) -> str:
    """SSE 鉴权：优先 Bearer；否则 ?ticket=；development 才允许 ?token=。"""
    from app.core.config import get_settings

    settings = get_settings()

    if credentials and credentials.credentials:
        payload = decode_access_token(credentials.credentials)
        if payload and payload.get("sub"):
            return await _require_active_user(session, str(payload["sub"]))
        raise HTTPException(401, "凭据无效或已过期")

    if ticket:
        payload = decode_sse_ticket(ticket)
        if not payload or not payload.get("sub"):
            raise HTTPException(401, "SSE ticket 无效或已过期")
        # task_id 与票面绑定在端点内再核（Depends 拿不到 path 参数时由端点调用 assert）
        request.state.sse_ticket_task_id = str(payload["tid"])
        return await _require_active_user(session, str(payload["sub"]))

    if token_query:
        if settings.environment == "production":
            raise HTTPException(
                401,
                "生产环境禁止 SSE ?token=，请先领取 short-lived ticket",
            )
        payload = decode_access_token(token_query)
        if payload and payload.get("sub"):
            return await _require_active_user(session, str(payload["sub"]))
        raise HTTPException(401, "凭据无效或已过期")

    fallback = _dev_system_fallback(request)
    if fallback is not None:
        return fallback
    raise HTTPException(401, "未提供认证凭据")


async def get_crucible_context(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CrucibleContext:
    """提取全链路请求上下文 (CrucibleContext)。"""
    from app.contexts.identity.models import User
    from app.shared.context import CrucibleContext, set_current_context

    ctx = CrucibleContext.from_headers(request.headers)

    if credentials and credentials.credentials:
        payload = decode_access_token(credentials.credentials)
        if payload and payload.get("sub"):
            user_id = str(payload["sub"])
            user = await session.get(User, user_id)
            if user is None or not user.is_active:
                raise HTTPException(401, "用户不存在或已停用")
            ctx.user_id = user_id
            ctx.role = user.role or "viewer"
            ctx.is_admin = bool(user.is_admin or user.role == "admin")
            ctx.auth_token = credentials.credentials
            set_current_context(ctx)
            return ctx
        raise HTTPException(401, "凭据无效或已过期")

    fallback = _dev_system_fallback(request)
    if fallback is not None:
        ctx.user_id = fallback
        ctx.role = "admin"
        ctx.is_admin = True
        set_current_context(ctx)
        return ctx

    raise HTTPException(401, "未提供认证凭据")


CurrentContext = Annotated[CrucibleContext, Depends(get_crucible_context)]


# 各 Context 直接 Depends(CurrentUserId) 注入
CurrentUserId = Annotated[str, Depends(get_current_user_id)]


async def get_current_admin_id(
    user_id: CurrentUserId,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> str:
    """平台级配置只允许活跃管理员修改或读取。"""
    from app.contexts.identity.models import User

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "用户不存在或已停用")
    if not user.is_admin and user.role != "admin":
        raise HTTPException(403, "仅管理员可访问平台设置")
    return user_id


CurrentAdminId = Annotated[str, Depends(get_current_admin_id)]

