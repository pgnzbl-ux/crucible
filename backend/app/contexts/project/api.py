from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.shared.deps import CurrentUserId
from .repository import ProjectRepository
from .schemas import (
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdateRequest,
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


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    p = await svc.get_project(project_id)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    request: ProjectUpdateRequest,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    p = await svc.update_project(project_id, request)
    if not p:
        raise HTTPException(404, "项目不存在")
    return p


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    svc: Annotated[ProjectService, Depends(get_project_service)],
) -> None:
    deleted = await svc.delete_project(project_id)
    if not deleted:
        raise HTTPException(404, "项目不存在")
