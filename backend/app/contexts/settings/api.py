from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.shared.deps import CurrentUserId, get_current_admin_id, get_current_user_id

from .repository import SettingsRepository
from .schemas import (
    CredentialCreateRequest,
    CredentialListResponse,
    CredentialResponse,
    CredentialUpdateRequest,
    LlmProviderCreateRequest,
    LlmProviderListResponse,
    LlmProviderResponse,
    LlmProviderTestRequest,
    LlmProviderTestResult,
    LlmProviderUpdateRequest,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdateRequest,
)
from .service import SettingsService

router = APIRouter(
    prefix="/settings",
    tags=["settings"],
    dependencies=[Depends(get_current_user_id)],
)


async def get_settings_repo(session: Annotated[AsyncSession, Depends(get_db_session)]) -> SettingsRepository:
    return SettingsRepository(session)


async def get_settings_service(repo: Annotated[SettingsRepository, Depends(get_settings_repo)]) -> SettingsService:
    return SettingsService(repo)


# ── LLM Provider ──

@router.get("/llm/providers", response_model=LlmProviderListResponse, dependencies=[Depends(get_current_admin_id)])
async def list_providers(
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderListResponse:
    items, total = await svc.list_providers()
    return LlmProviderListResponse(items=items, total=total)


@router.post("/llm/providers", response_model=LlmProviderResponse, status_code=201, dependencies=[Depends(get_current_admin_id)])
async def create_provider(
    request: LlmProviderCreateRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderResponse:
    try:
        return await svc.create_provider(request)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.put("/llm/providers/{provider_id}", response_model=LlmProviderResponse, dependencies=[Depends(get_current_admin_id)])
async def update_provider(
    provider_id: str,
    request: LlmProviderUpdateRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderResponse:
    try:
        provider = await svc.update_provider(provider_id, request)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not provider:
        raise HTTPException(404, "Provider 不存在")
    return provider


@router.delete("/llm/providers/{provider_id}", status_code=204, dependencies=[Depends(get_current_admin_id)])
async def delete_provider(
    provider_id: str,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> None:
    deleted = await svc.delete_provider(provider_id)
    if not deleted:
        raise HTTPException(404, "Provider 不存在")


@router.post("/llm/providers/{provider_id}/activate", response_model=LlmProviderResponse, dependencies=[Depends(get_current_admin_id)])
async def activate_provider(
    provider_id: str,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderResponse:
    provider = await svc.activate_provider(provider_id)
    if not provider:
        raise HTTPException(404, "Provider 不存在")
    return provider


@router.post("/llm/providers/{provider_id}/test", response_model=LlmProviderTestResult, dependencies=[Depends(get_current_admin_id)])
async def test_provider(
    provider_id: str,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderTestResult:
    return await svc.test_connection(provider_id=provider_id)


@router.post("/llm/test", response_model=LlmProviderTestResult, dependencies=[Depends(get_current_admin_id)])
async def test_connection(
    request: LlmProviderTestRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderTestResult:
    """临时参数测试连接（未保存前先验证）"""
    return await svc.test_connection(
        base_url=request.base_url,
        api_key=request.api_key,
        model=request.model,
        temperature=request.temperature,
        effort=request.effort,
    )


# ── Credential（任务级凭据，P1-6 Credential Proxy） ──

@router.get("/credentials", response_model=CredentialListResponse)
async def list_credentials(
    svc: Annotated[SettingsService, Depends(get_settings_service)],
    user_id: CurrentUserId,
) -> CredentialListResponse:
    items, total = await svc.list_credentials(user_id)
    return CredentialListResponse(items=items, total=total)


@router.post("/credentials", response_model=CredentialResponse, status_code=201)
async def create_credential(
    request: CredentialCreateRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
    user_id: CurrentUserId,
) -> CredentialResponse:
    return await svc.create_credential(user_id, request)


@router.put("/credentials/{credential_id}", response_model=CredentialResponse)
async def update_credential(
    credential_id: str,
    request: CredentialUpdateRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
    user_id: CurrentUserId,
) -> CredentialResponse:
    cred = await svc.update_credential(user_id, credential_id, request)
    if not cred:
        raise HTTPException(404, "凭据不存在")
    return cred


@router.delete("/credentials/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: str,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
    user_id: CurrentUserId,
) -> None:
    deleted = await svc.delete_credential(user_id, credential_id)
    if not deleted:
        raise HTTPException(404, "凭据不存在")


@router.get("/runtime", response_model=RuntimeSettingsResponse, dependencies=[Depends(get_current_admin_id)])
async def get_runtime_settings(
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> RuntimeSettingsResponse:
    return await svc.get_runtime_settings()


@router.put("/runtime", response_model=RuntimeSettingsResponse, dependencies=[Depends(get_current_admin_id)])
async def update_runtime_settings(
    request: RuntimeSettingsUpdateRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> RuntimeSettingsResponse:
    try:
        return await svc.update_runtime_settings(request)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
