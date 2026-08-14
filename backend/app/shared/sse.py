"""
SSE 实时事件推送工具。

设计：
- 每个客户端独立 Redis Pub/Sub 订阅（与 concurrency.md §5 一致：客户端断开立即清理）
- 15s 心跳防代理超时（api-design.md §5）
- 用 async generator yield SSE 帧，FastAPI StreamingResponse 包装
- 频道规则：task.{task_id}.events（与 agent/tasks.py 发布侧对齐）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import redis.asyncio as aioredis
from fastapi import Request

from app.core.config import get_settings
from app.shared.events import Event, event_bus

logger = logging.getLogger(__name__)
settings = get_settings()

# 心跳间隔（秒）—— 防 nginx/cloudflare 默认 60s 代理超时切断
SSE_HEARTBEAT_SECONDS = 15


def _sse_frame(event_type: str, data: dict[str, Any] | str, event_id: str | None = None) -> str:
    """构造一条 SSE 帧(按 WHATWG HTML5 EventSource 规范)。

    重要:不发 `event:` 具名事件行 —— 具名事件只触发 addEventListener('事件名'),
    不触发 onmessage,会导致前端 onmessage 永远收不到。改为把 event_type 塞进
    data 体的 `type` 字段,前端用 onmessage 通杀 + 按 parsed.type 分发。
    """
    if isinstance(data, dict):
        # 把事件类型塞进 data 体(前端 SSEEvent.type 依赖此字段)
        data = {**data, "type": event_type}
        data_str = json.dumps(data, ensure_ascii=False, default=str)
    else:
        data_str = data
    parts: list[str] = []
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append(f"data: {data_str}")
    return "\n".join(parts) + "\n\n"


async def _replay_history(task_id: str, limit: int = 1000) -> list[str]:
    """订阅前先回放历史事件（DB 已落库），让前端一连接就拿到完整进度

    通过 DB 读 AgentEvent 行 — 比 Redis Pub/Sub 持久可靠（Pub/Sub 离线即丢）。
    取当前 run 最近 limit 条（按 sequence 升序回放），避免历史重试的 phase.updated 混进事件流。
    """
    # 这里不能直接 await ORM session，因为 SSE generator 在 FastAPI 请求内；
    # 通过独立 engine + session_factory 短生命周期会话读一次即可。
    from app.core.database import async_session_factory
    from app.contexts.task.repository import TaskRepository

    frames: list[str] = []
    try:
        async with async_session_factory() as session:
            events = await TaskRepository(session).get_events_for_task(task_id, limit)
    except Exception as e:  # noqa: BLE001 — DB 读失败不影响后续实时流
        logger.warning(f"SSE 历史回放失败（仅实时流可用）: {e}")
        return frames

    for ev in events:
        try:
            payload = json.loads(ev.payload)
        except (ValueError, TypeError):
            payload = {}
        frames.append(
            _sse_frame(
                event_type=ev.event_type,
                data={
                    "sequence": ev.sequence,
                    "run_id": ev.run_id,
                    "event": payload,
                    "replayed": True,
                },
                event_id=str(ev.sequence),
            )
        )
    return frames


async def stream_task_events(
    task_id: str,
    request: Request,
) -> AsyncIterator[str]:
    """SSE 事件流 generator

    流程：
      1. 验证 task 存在（不存在 → 404 帧 + 关闭）
      2. 回放历史事件
      3. 订阅 Redis 频道 task.{task_id}.events
      4. 循环：get_message(timeout=1.0) → yield 帧；每 15s yield 心跳
      5. 客户端断开（request.is_disconnected）→ 退出
    """
    # 1. 验证 task 存在
    from app.core.database import async_session_factory
    from app.contexts.task.repository import TaskRepository

    try:
        async with async_session_factory() as session:
            task = await TaskRepository(session).get_by_id(task_id)
            if task is None:
                yield _sse_frame("error", {"code": "TASK_NOT_FOUND", "message": f"Task {task_id} not found"})
                return
    except Exception as e:  # noqa: BLE001
        logger.exception(f"SSE 验证 task 失败: {e}")
        yield _sse_frame("error", {"code": "INTERNAL_ERROR", "message": "服务暂时不可用"})
        return

    # 2. 回放历史
    for frame in await _replay_history(task_id):
        yield frame

    # 3. 订阅 Redis
    channel = f"task.{task_id}.events"
    r = aioredis.from_url(settings.redis_url, decode_responses=True)
    pubsub = r.pubsub()
    try:
        await pubsub.subscribe(channel)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"SSE Redis 订阅失败: {e}")
        yield _sse_frame("error", {"code": "SUBSCRIBE_FAILED", "message": "事件流订阅失败"})
        await pubsub.close()
        await r.close()
        return

    logger.info(f"SSE 订阅 task={task_id} channel={channel}")
    yield _sse_frame("ready", {"task_id": task_id, "channel": channel})

    # 4. 主循环：每 1s 检查新消息，每 15s 发心跳
    last_heartbeat = asyncio.get_event_loop().time()
    try:
        while True:
            # 客户端断开检测
            if await request.is_disconnected():
                logger.info(f"SSE 客户端断开 task={task_id}")
                break

            try:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"SSE pubsub.get_message 异常: {e}")
                msg = None

            if msg and msg.get("type") == "message":
                # msg["data"] 是 Event.to_json() 字符串
                try:
                    event_dict = json.loads(msg["data"])
                    payload_data = event_dict.get("payload", {})
                    yield _sse_frame(
                        event_type=event_dict.get("event_type", "event"),
                        data={
                            "sequence": payload_data.get("sequence"),
                            "run_id": payload_data.get("run_id"),
                            "event": payload_data.get("event"),
                            "correlation_id": event_dict.get("correlation_id"),
                        },
                        event_id=str(payload_data.get("sequence") or ""),
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"SSE 解析消息失败: {e}")

            # 心跳
            now = asyncio.get_event_loop().time()
            if now - last_heartbeat >= SSE_HEARTBEAT_SECONDS:
                yield ": heartbeat\n\n"
                last_heartbeat = now
    finally:
        # 5. 清理订阅 — 防 Redis 连接泄漏
        try:
            await pubsub.unsubscribe(channel)
        except Exception:  # noqa: BLE001
            pass
        try:
            await pubsub.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await r.close()
        except Exception:  # noqa: BLE001
            pass
        logger.info(f"SSE 清理完成 task={task_id}")