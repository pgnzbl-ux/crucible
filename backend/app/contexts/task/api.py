from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.shared.deps import CurrentUserId
from app.shared.object_store import ObjectStoreError
from app.shared.sse import stream_task_events

from .repository import TaskRepository
from .schemas import TaskCreateRequest, TaskDetail, TaskListResponse, TaskStatsResponse
from .service import TaskDispatchError, TaskService

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
    user_id: CurrentUserId,
) -> TaskDetail:
    """创建代码审计或定向验证任务。"""
    try:
        return await svc.create_task(request, user_id)
    except TaskDispatchError as e:
        raise HTTPException(503, str(e)) from e


@router.get("/", response_model=TaskListResponse)
async def list_tasks(
    svc: Annotated[TaskService, Depends(get_task_service)],
    user_id: CurrentUserId,
    status: str | None = Query(None, description="单状态或逗号分隔多状态，如 pending,queued"),
    priority: str | None = Query(None),
    q: str | None = Query(None, description="项目地址关键词"),
    date_from: str | None = Query(None, description="创建日起 YYYY-MM-DD"),
    date_to: str | None = Query(None, description="创建日止 YYYY-MM-DD"),
    task_type: str | None = Query(None, description="verify | discovery(discovery-spec §9.1)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> TaskListResponse:
    """获取任务列表"""
    items = await svc.list_tasks(
        user_id, status, priority, limit, offset, q=q, date_from=date_from, date_to=date_to,
        task_type=task_type,
    )
    return items


@router.get("/stats", response_model=TaskStatsResponse)
async def task_stats(
    svc: Annotated[TaskService, Depends(get_task_service)],
    user_id: CurrentUserId,
) -> TaskStatsResponse:
    """工作台状态计数（排除 archived）。必须声明在 /{task_id} 之前。"""
    return await svc.task_stats(user_id)


_UPLOAD_MAX_BYTES = 200 * 1024 * 1024


@router.post("/upload", response_model=TaskDetail, status_code=202)
async def create_task_from_upload(
    svc: Annotated[TaskService, Depends(get_task_service)],
    user_id: CurrentUserId,
    file: UploadFile = File(..., description="源码包 zip / tar / tar.gz，≤200MB"),
    vulnerability_description: Annotated[str | None, Form()] = None,
    task_type: Annotated[str, Form(pattern=r"^(verify|discovery)$")] = "verify",
    name: Annotated[str | None, Form()] = None,
    priority: Annotated[str, Form()] = "medium",
    vulnerability_reasoning: Annotated[str | None, Form()] = None,
    credential_refs: Annotated[str | None, Form(description="JSON 数组，凭据 id 列表")] = None,
) -> TaskDetail:
    """上传本地源码包并创建代码审计或定向验证。"""
    import json as _json

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

    refs: list[str] = []
    if credential_refs:
        try:
            parsed = _json.loads(credential_refs)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "credential_refs 必须是 JSON 数组") from exc
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise HTTPException(400, "credential_refs 必须是 JSON 字符串数组")
        refs = parsed

    try:
        return await svc.create_task_from_upload(
            owner_id=user_id,
            filename=file.filename or "source.zip",
            data=data,
            vulnerability_description=vulnerability_description,
            task_type=task_type,
            name=name,
            priority=priority,
            vulnerability_reasoning=vulnerability_reasoning,
            credential_refs=refs,
        )
    except TaskDispatchError as e:
        raise HTTPException(503, str(e)) from e
    except ObjectStoreError as e:
        raise HTTPException(503, f"源码包入库失败: {e}") from e


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
    user_id: CurrentUserId,
) -> TaskDetail:
    """获取任务详情"""
    task = await svc.get_task(task_id, user_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.get("/{task_id}/events")
async def get_task_events(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
    user_id: CurrentUserId,
    limit: int = Query(1000, ge=1, le=1000),
) -> list[dict[str, Any]]:
    """获取任务 Agent 事件流（历史分页，SSE 不可用时降级方案）"""
    events = await svc.get_task_events(task_id, user_id, limit)
    if events is None:
        raise HTTPException(404, "任务不存在")
    return events


@router.get("/{task_id}/events/stream")
async def stream_task_events_endpoint(
    task_id: str,
    request: Request,
    user_id: CurrentUserId,
    svc: Annotated[TaskService, Depends(get_task_service)],
) -> StreamingResponse:
    """SSE 实时事件推送（P0-1）

    - 鉴权：?token=<jwt>（EventSource 不能注入 header，见 shared/deps.py）
    - 启动时回放历史（DB）
    - 订阅 Redis 频道 task.{id}.events 转发
    - 15s 心跳防代理超时
    - 客户端断开立即清理 Redis 订阅（防连接泄漏）
    """
    if await svc.get_task(task_id, user_id) is None:
        raise HTTPException(404, "任务不存在")
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
    user_id: CurrentUserId,
) -> TaskDetail:
    """取消任务"""
    try:
        task = await svc.cancel_task(task_id, user_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.post("/{task_id}/retry", status_code=202)
async def retry_task(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
    user_id: CurrentUserId,
    from_node: str | None = None,
) -> dict:
    """重试任务。

    默认从节点 0 整条重跑；带 from_node=env_ready|audit|reproduce|report 时，
    复用上一 run 该节点之前的产出，只重跑该节点及之后（不重跑 clone/画像）。
    """
    try:
        new_run_id = await svc.retry_task(task_id, user_id, from_node=from_node)
    except TaskDispatchError as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"task_id": task_id, "run_id": new_run_id, "status": "retrying", "from_node": from_node}


@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
    user_id: CurrentUserId,
    hard: bool = Query(False, description="true=物理删除,默认软删 archived"),
    x_confirm: Annotated[str | None, Header(alias="X-Confirm")] = None,
) -> None:
    """删除任务。默认软删(status=archived);hard=true 需 X-Confirm: true header。"""
    if hard and x_confirm != "true":
        raise HTTPException(428, "硬删需 X-Confirm: true header")
    try:
        deleted = await svc.delete_task(task_id, user_id, hard=hard)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not deleted:
        raise HTTPException(404, "任务不存在")


@router.get("/{task_id}/runs/{run_id}/nodes")
async def get_run_nodes(
    task_id: str,
    run_id: str,
    svc: Annotated[TaskService, Depends(get_task_service)],
    user_id: CurrentUserId,
) -> list[dict]:
    """获取某 run 的节点状态(前端步骤条数据源)。"""
    nodes = await svc.get_run_nodes(task_id, run_id, user_id)
    if nodes is None:
        raise HTTPException(404, "任务不存在")
    return nodes
