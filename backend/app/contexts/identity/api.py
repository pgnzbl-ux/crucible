from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from .repository import IdentityRepository
from .schemas import AuthSetupResponse, LoginRequest, RegisterRequest, TokenResponse, UserResponse
from .service import IdentityService

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


# ── 依赖注入 ──

async def get_identity_repo(session: Annotated[AsyncSession, Depends(get_db_session)]) -> IdentityRepository:
    return IdentityRepository(session)


async def get_identity_svc(repo: Annotated[IdentityRepository, Depends(get_identity_repo)]) -> IdentityService:
    return IdentityService(repo)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    svc: Annotated[IdentityService, Depends(get_identity_svc)],
) -> UserResponse:
    if not credentials:
        raise HTTPException(401, "未提供认证凭据")
    user = await svc.get_current_user(credentials.credentials)
    if not user:
        raise HTTPException(401, "凭据无效或已过期")
    return user


# ── 端点 ──

@router.get("/setup", response_model=AuthSetupResponse)
async def auth_setup(
    svc: Annotated[IdentityService, Depends(get_identity_svc)],
) -> AuthSetupResponse:
    return AuthSetupResponse(needs_setup=await svc.needs_setup())


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    request: RegisterRequest,
    svc: Annotated[IdentityService, Depends(get_identity_svc)],
) -> UserResponse:
    try:
        return await svc.register(request)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    svc: Annotated[IdentityService, Depends(get_identity_svc)],
) -> TokenResponse:
    try:
        return await svc.login(request)
    except ValueError as e:
        raise HTTPException(401, str(e))


@router.get("/me", response_model=UserResponse)
async def me(
    user: Annotated[UserResponse, Depends(get_current_user)],
) -> UserResponse:
    return user
