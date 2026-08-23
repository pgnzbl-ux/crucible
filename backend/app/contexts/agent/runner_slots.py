"""AI 容器与同靶场复现槽（Redis ZSET 租约）。

租约只回收 worker 崩溃后遗留的计数，不是执行超时：持有者会持续续租，
AI 本身可无限运行；任务取消仍由 runtime_cleanup 主动拆容器。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

AGENT_RUNNER_SLOTS_KEY = "crucible:agent_runner_slots"
AGENT_RUNNER_LIMIT_KEY = "crucible:runtime:max_agent_runners"
REPRODUCE_LIMIT_KEY = "crucible:runtime:reproduce_per_lab"

_LEASE_SECONDS = 120
_HEARTBEAT_SECONDS = 30
_POLL_SECONDS = 0.5

logger = logging.getLogger(__name__)

_ACQUIRE_LUA = """
local slots_key = KEYS[1]
local limit_key = KEYS[2]
local holder = ARGV[1]
local now = tonumber(ARGV[2])
local lease_seconds = tonumber(ARGV[3])
local fallback_limit = tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', slots_key, '-inf', now - lease_seconds)
if redis.call('ZSCORE', slots_key, holder) then
  redis.call('ZADD', slots_key, now, holder)
  return 1
end
local limit = tonumber(redis.call('GET', limit_key)) or fallback_limit
if limit == nil or limit < 1 then
  limit = 1
end
if redis.call('ZCARD', slots_key) >= limit then
  return 0
end
redis.call('ZADD', slots_key, now, holder)
return 1
"""

_HEARTBEAT_LUA = """
if redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
  return 1
end
return 0
"""

_injected: Any = None


def set_redis_client(client: Any) -> None:
    """测试注入；传 None 恢复每调用新建连接。"""
    global _injected
    _injected = client


@asynccontextmanager
async def _redis() -> AsyncIterator[Any]:
    if _injected is not None:
        yield _injected
        return
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    client = aioredis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=5,
    )
    try:
        yield client
    finally:
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close is not None:
            await close()


async def set_runtime_limits(*, agent_runners: int, reproduce_per_lab: int) -> None:
    """设置 API/任务准入调用：把 DB 预算同步给跨进程调度器。"""
    async with _redis() as redis:
        await redis.mset({
            AGENT_RUNNER_LIMIT_KEY: str(max(1, int(agent_runners))),
            REPRODUCE_LIMIT_KEY: str(max(1, int(reproduce_per_lab))),
        })


async def _try_acquire(
    slots_key: str,
    limit_key: str,
    holder: str,
    fallback_limit: int,
) -> bool:
    async with _redis() as redis:
        result = await redis.eval(
            _ACQUIRE_LUA,
            2,
            slots_key,
            limit_key,
            holder,
            str(time.time()),
            str(_LEASE_SECONDS),
            str(max(1, int(fallback_limit))),
        )
        return int(result) == 1


async def _heartbeat(slots_key: str, holder: str, stopped: asyncio.Event) -> None:
    while True:
        try:
            await asyncio.wait_for(stopped.wait(), timeout=_HEARTBEAT_SECONDS)
            return
        except asyncio.TimeoutError:
            pass
        try:
            async with _redis() as redis:
                await redis.eval(
                    _HEARTBEAT_LUA, 1, slots_key, holder, str(time.time())
                )
        except Exception:  # noqa: BLE001 — 短暂 Redis 故障时继续续租重试
            logger.warning("AI 调度槽续租失败 holder=%s", holder, exc_info=True)


async def _release(slots_key: str, holder: str) -> None:
    async with _redis() as redis:
        await redis.zrem(slots_key, holder)


@asynccontextmanager
async def _leased_slot(
    *,
    slots_key: str,
    limit_key: str,
    fallback_limit: int,
    label: str,
    on_wait: Callable[[], None] | None = None,
) -> AsyncIterator[str]:
    holder = f"{label}:{uuid.uuid4().hex}"
    waiting_reported = False
    while not await _try_acquire(slots_key, limit_key, holder, fallback_limit):
        if on_wait is not None and not waiting_reported:
            on_wait()
            waiting_reported = True
        await asyncio.sleep(_POLL_SECONDS)

    stopped = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(slots_key, holder, stopped))
    try:
        yield holder
    finally:
        stopped.set()
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        try:
            await _release(slots_key, holder)
        except Exception:  # noqa: BLE001 — 租约会自动过期，不能反向覆盖节点结果
            logger.warning("AI 调度槽释放失败 holder=%s", holder, exc_info=True)


@asynccontextmanager
async def agent_runner_slot(
    *, task_id: str | None, node_key: str, on_wait: Callable[[], None] | None = None
) -> AsyncIterator[str]:
    from app.core.config import get_settings

    label = f"{task_id or 'unknown'}:{node_key}"
    async with _leased_slot(
        slots_key=AGENT_RUNNER_SLOTS_KEY,
        limit_key=AGENT_RUNNER_LIMIT_KEY,
        fallback_limit=get_settings().agent_runner_concurrency_limit,
        label=label,
        on_wait=on_wait,
    ) as holder:
        yield holder


@asynccontextmanager
async def reproduce_slot(
    scope_id: str, *, on_wait: Callable[[], None] | None = None
) -> AsyncIterator[str]:
    safe_scope = "".join(c if c.isalnum() or c in "-_" else "_" for c in scope_id)[:128]
    slots_key = f"crucible:reproduce_slots:{safe_scope or 'unknown'}"
    async with _leased_slot(
        slots_key=slots_key,
        limit_key=REPRODUCE_LIMIT_KEY,
        fallback_limit=1,
        label=safe_scope or "unknown",
        on_wait=on_wait,
    ) as holder:
        yield holder
