from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.shared.deps import CurrentUserId

from .errors import LabBusyError, LabNotFoundError
from .schemas import LabActionResponse, LabListResponse, LabResponse
from .service import LabService

router = APIRouter(prefix="/labs", tags=["labs"])


async def get_lab_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> LabService:
    return LabService(session)


async def _execute(operation: Awaitable[Any]) -> Any:
    try:
        return await operation
    except LabBusyError as exc:
        raise HTTPException(
            409,
            detail={
                "code": "LAB_IN_USE",
                "message": "靶场正被运行中的任务占用",
                "task_ids": exc.task_ids,
            },
        ) from exc
    except LabNotFoundError as exc:
        raise HTTPException(404, detail="靶场不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.get("", response_model=LabListResponse)
async def list_labs(
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabListResponse:
    return LabListResponse(items=await service.list_grouped(user_id))


@router.get("/{lab_id}", response_model=LabResponse)
async def get_lab(
    lab_id: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> dict:
    return await _execute(service.get_detail(lab_id, owner_id=user_id))


@router.post("/{lab_id}/actions/stop", response_model=LabActionResponse)
async def stop_lab(
    lab_id: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabActionResponse:
    status = await _execute(service.stop_lab(lab_id, owner_id=user_id))
    return LabActionResponse(status=status)


@router.post("/{lab_id}/actions/start", response_model=LabActionResponse)
async def start_lab(
    lab_id: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabActionResponse:
    status = await _execute(service.start_lab(lab_id, owner_id=user_id))
    return LabActionResponse(status=status)


@router.post("/{lab_id}/actions/rebuild", response_model=LabActionResponse)
async def rebuild_lab(
    lab_id: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabActionResponse:
    status = await _execute(service.rebuild_lab(lab_id, owner_id=user_id))
    return LabActionResponse(status=status)


@router.delete("/{lab_id}", response_model=LabActionResponse)
async def destroy_lab(
    lab_id: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabActionResponse:
    status = await _execute(service.destroy_lab(lab_id, owner_id=user_id))
    return LabActionResponse(status=status)


async def _container_action(
    service: LabService,
    lab_id: str,
    name: str,
    action: str,
    owner_id: str,
) -> LabActionResponse:
    status = await _execute(
        service.container_action(lab_id, name, action=action, owner_id=owner_id)
    )
    return LabActionResponse(status=status)


@router.post(
    "/{lab_id}/containers/{name}/actions/stop",
    response_model=LabActionResponse,
)
async def stop_container(
    lab_id: str,
    name: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabActionResponse:
    return await _container_action(service, lab_id, name, "stop", user_id)


@router.post(
    "/{lab_id}/containers/{name}/actions/start",
    response_model=LabActionResponse,
)
async def start_container(
    lab_id: str,
    name: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabActionResponse:
    return await _container_action(service, lab_id, name, "start", user_id)


@router.post(
    "/{lab_id}/containers/{name}/actions/restart",
    response_model=LabActionResponse,
)
async def restart_container(
    lab_id: str,
    name: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabActionResponse:
    return await _container_action(service, lab_id, name, "restart", user_id)


@router.delete(
    "/{lab_id}/containers/{name}",
    response_model=LabActionResponse,
)
async def remove_container(
    lab_id: str,
    name: str,
    service: Annotated[LabService, Depends(get_lab_service)],
    user_id: CurrentUserId,
) -> LabActionResponse:
    return await _container_action(service, lab_id, name, "rm", user_id)
