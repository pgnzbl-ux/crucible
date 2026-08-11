"""
共享依赖 — 跨 Context 的 FastAPI Depends。

当前提供：
- get_current_user_id：从 Authorization Bearer 或 ?token= 解析 JWT → 返回 user_id
  - 优先 Authorization header（常规 API）
  - 退回 query ?token=（SSE / EventSource 不能注入 header，见 frontend useTaskEvents）

设计：
- 鉴权逻辑集中在此，各 Context 只 Depends 即可，不重复实现
- 返回 user_id（str），不返回 User 对象 —— Context 只需 owner 维度
- 凭据缺失/失效统一抛 401
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_access_token

# auto_error=False：SSE 端点用 ?token=，缺 header 不在此抛错，由下游统一处理
_bearer = HTTPBearer(auto_error=False)


async def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    token_query: Annotated[str | None, Query(alias="token", description="JWT（SSE 端点用，EventSource 无法注入 header）")] = None,
) -> str:
    """解析当前登录用户 id。

    取值优先级：
      1. Authorization: Bearer <jwt>
      2. ?token=<jwt>（SSE 专用）
      3. X-User-Id（仅 ENVIRONMENT=development 兜底，未登录时回退 system）

    开发模式（未配置 AUTH_SECRET 或 environment=development）允许回退 "system"，
    便于 Mock 全链路冒烟；生产强制鉴权。
    """
    from app.core.config import get_settings

    settings = get_settings()
    jwt_str: str | None = None
    if credentials and credentials.credentials:
        jwt_str = credentials.credentials
    elif token_query:
        jwt_str = token_query

    if jwt_str:
        payload = decode_access_token(jwt_str)
        if payload and payload.get("sub"):
            return str(payload["sub"])
        # token 解析失败 → 401（不静默回退，防越权）
        raise HTTPException(401, "凭据无效或已过期")

    # 未提供 token
    if settings.environment == "development":
        # 开发模式回退（保留原 owner_id="system" 行为，不破坏 Mock 冒烟）
        return "system"

    raise HTTPException(401, "未提供认证凭据")


# 各 Context 直接 Depends(CurrentUserId) 注入
CurrentUserId = Annotated[str, Depends(get_current_user_id)]
