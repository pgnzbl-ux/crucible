from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from .repository import SettingsRepository
from .schemas import (
    LlmProviderCreateRequest,
    LlmProviderListResponse,
    LlmProviderResponse,
    LlmProviderTestRequest,
    LlmProviderTestResult,
    LlmProviderUpdateRequest,
)
from .service import SettingsService

router = APIRouter(prefix="/settings", tags=["settings"])


async def get_settings_repo(session: Annotated[AsyncSession, Depends(get_db_session)]) -> SettingsRepository:
    return SettingsRepository(session)


async def get_settings_service(repo: Annotated[SettingsRepository, Depends(get_settings_repo)]) -> SettingsService:
    return SettingsService(repo)


# ── LLM Provider ──

@router.get("/llm/providers", response_model=LlmProviderListResponse)
async def list_providers(
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderListResponse:
    items, total = await svc.list_providers()
    return LlmProviderListResponse(items=items, total=total)


@router.post("/llm/providers", response_model=LlmProviderResponse, status_code=201)
async def create_provider(
    request: LlmProviderCreateRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderResponse:
    return await svc.create_provider(request)


@router.put("/llm/providers/{provider_id}", response_model=LlmProviderResponse)
async def update_provider(
    provider_id: str,
    request: LlmProviderUpdateRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderResponse:
    provider = await svc.update_provider(provider_id, request)
    if not provider:
        raise HTTPException(404, "Provider 不存在")
    return provider


@router.delete("/llm/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> None:
    deleted = await svc.delete_provider(provider_id)
    if not deleted:
        raise HTTPException(404, "Provider 不存在")


@router.post("/llm/providers/{provider_id}/activate", response_model=LlmProviderResponse)
async def activate_provider(
    provider_id: str,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderResponse:
    try:
        provider = await svc.activate_provider(provider_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not provider:
        raise HTTPException(404, "Provider 不存在")
    return provider


@router.post("/llm/providers/{provider_id}/test", response_model=LlmProviderTestResult)
async def test_provider(
    provider_id: str,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderTestResult:
    return await svc.test_connection(provider_id=provider_id)


@router.post("/llm/test", response_model=LlmProviderTestResult)
async def test_connection(
    request: LlmProviderTestRequest,
    svc: Annotated[SettingsService, Depends(get_settings_service)],
) -> LlmProviderTestResult:
    """临时参数测试连接（未保存前先验证）"""
    return await svc.test_connection(
        base_url=request.base_url,
        api_key=request.api_key,
        model=request.model,
    )
