from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.shared.deps import CurrentUserId
from app.shared.object_store import ObjectStoreError

from .repository import ProjectRepository
from .schemas import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
    SourceArtifactListResponse,
)
from .service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


async def get_project_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProjectService:
    return ProjectService(ProjectRepository(session))


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ProjectListResponse:
    return await svc.list_projects(user_id, limit, offset)


@router.post("/", response_model=ProjectResponse, status_code=201)
async def create_project(
    request: ProjectCreateRequest,
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
) -> ProjectResponse:
    return await svc.create_project(request, user_id)


_UPLOAD_MAX_BYTES = 200 * 1024 * 1024


@router.post("/upload", response_model=ProjectResponse, status_code=201)
async def upload_project(
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
    file: UploadFile = File(..., description="源码包 zip / tar / tar.gz，≤200MB"),
    name: Annotated[str, Form(min_length=1, max_length=255)] = ...,
    description: Annotated[str | None, Form()] = None,
) -> ProjectResponse:
    """登记本地源码包为项目，不创建验证任务。"""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > _UPLOAD_MAX_BYTES:
            raise HTTPException(413, "源码包超过 200MB 限制")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "源码包为空")
    try:
        project, _result = await svc.ingest_uploaded_source(
            owner_id=user_id,
            filename=file.filename or "source.zip",
            data=data,
            name=name,
            description=description,
        )
    except ObjectStoreError as e:
        raise HTTPException(503, f"源码包入库失败: {e}") from e
    return ProjectResponse.model_validate(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
) -> ProjectResponse:
    p = await svc.get_project(project_id, user_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.get("/{project_id}/artifacts", response_model=SourceArtifactListResponse)
async def list_project_artifacts(
    project_id: str,
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
) -> SourceArtifactListResponse:
    items = await svc.list_artifacts(project_id, user_id)
    if items is None:
        raise HTTPException(404, "项目不存在")
    return SourceArtifactListResponse(items=items, total=len(items))


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    request: ProjectUpdateRequest,
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
) -> ProjectResponse:
    p = await svc.update_project(project_id, request, user_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    svc: Annotated[ProjectService, Depends(get_project_service)],
    user_id: CurrentUserId,
) -> None:
    deleted = await svc.delete_project(project_id, user_id)
    if not deleted:
        raise HTTPException(404, "项目不存在")
