from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.security import decode_access_token
from app.shared.sse import stream_task_events
from .repository import TaskRepository
from .schemas import TaskCreateRequest, TaskDetail, TaskListResponse
from .service import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ── 依赖注入 ──

async def get_task_repo(session: Annotated[AsyncSession, Depends(get_db_session)]) -> TaskRepository:
    return TaskRepository(session)


async def get_task_service(repo: Annotated[TaskRepository, Depends(get_task_repo)]) -> TaskService:
    return TaskService(repo)


# ── 端点 ──

@router.post("/", response_model=TaskDetail, status_code=202)
async def create_task(
    request: TaskCreateRequest,
    svc: Annotated[TaskService, Depends(get_task_service)],
) -> TaskDetail:
    """创建漏洞验证任务"""
    # TODO: 从 JWT token 解析 owner_id
    owner_id = "system"  # 临时占位
    return await svc.create_task(request, owner_id)


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    svc: Annotated[TaskService, Depends(get_task_service)],
    status: str | None = Query(None),
    priority: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> TaskListResponse:
    """获取任务列表"""
    owner_id = "system"  # 临时占位
    return await svc.list_tasks(owner_id, status, priority, limit, offset)


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
) -> TaskDetail:
    """获取任务详情"""
    task = await svc.get_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/{task_id}/events")
async def get_task_events(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """获取任务 Agent 事件流（历史分页，SSE 不可用时降级方案）"""
    events = await svc.get_task_events(task_id, limit)
    return events


@router.get("/{task_id}/events/stream")
async def stream_task_events_endpoint(task_id: str, request: Request) -> StreamingResponse:
    """SSE 实时事件推送（P0-1）

    - 启动时回放历史（DB）
    - 订阅 Redis 频道 task.{id}.events 转发
    - 15s 心跳防代理超时
    - 客户端断开立即清理 Redis 订阅（防连接泄漏）
    """
    return StreamingResponse(
        stream_task_events(task_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx: 关闭代理缓冲，SSE 实时
        },
    )


@router.post("/{task_id}/cancel", response_model=TaskDetail)
async def cancel_task(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
) -> TaskDetail:
    """取消任务"""
    try:
        task = await svc.cancel_task(task_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not task:
        raise HTTPException(404, "任务不存在")
    return task
