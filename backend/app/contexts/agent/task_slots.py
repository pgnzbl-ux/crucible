"""平台验证任务运行槽（Redis SET + Lua）。

连接 settings.redis_url（db0），禁止写 Celery broker db1。
Celery 每个任务 asyncio.run 一次：禁止缓存跨 loop 的 asyncio Redis 客户端。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

SLOT_SET_KEY = "crucible:running_run_ids"
SWEEPER_LOCK_KEY = "crucible:runtime_sweeper"

_ACQUIRE_LUA = """
local key = KEYS[1]
local run_id = ARGV[1]
local max_slots = tonumber(ARGV[2])
if max_slots == nil or max_slots < 1 then
  max_slots = 1
end
if redis.call('SISMEMBER', key, run_id) == 1 then
  return 1
end
if redis.call('SCARD', key) >= max_slots then
  return 0
end
redis.call('SADD', key, run_id)
return 1
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

    client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield client
    finally:
        close = getattr(client, "aclose", None) or getattr(client, "close", None)
        if close is not None:
            await close()


ORCH_LOCK_PREFIX = "crucible:orch_lock:"


def _orch_lock_ttl_seconds() -> int:
    """对齐 Celery hard limit + 余量；env 调大硬限后锁不再提前过期。"""
    from app.core.config import get_settings

    return int(get_settings().celery_task_time_limit_seconds) + 300


async def try_acquire_orch_lock(run_id: str) -> bool:
    """acks_late 双 worker：同一 run 只允许一个编排器执行。"""
    async with _redis() as r:
        ok = await r.set(
            f"{ORCH_LOCK_PREFIX}{run_id}", "1", nx=True, ex=_orch_lock_ttl_seconds(),
        )
        return bool(ok)


async def release_orch_lock(run_id: str) -> None:
    async with _redis() as r:
        await r.delete(f"{ORCH_LOCK_PREFIX}{run_id}")


async def try_acquire_slot(run_id: str, max_slots: int) -> bool:
    async with _redis() as r:
        capped = max(1, int(max_slots))
        result = await r.eval(_ACQUIRE_LUA, 1, SLOT_SET_KEY, run_id, str(capped))
        return int(result) == 1


async def release_slot(run_id: str) -> None:
    async with _redis() as r:
        await r.srem(SLOT_SET_KEY, run_id)


async def reconcile_slots(running_run_ids: set[str]) -> None:
    async with _redis() as r:
        current = set(await r.smembers(SLOT_SET_KEY) or [])
        wanted = set(running_run_ids)
        for extra in current - wanted:
            await r.srem(SLOT_SET_KEY, extra)
        for missing in wanted - current:
            await r.sadd(SLOT_SET_KEY, missing)


async def try_sweeper_lock() -> bool:
    async with _redis() as r:
        ok = await r.set(SWEEPER_LOCK_KEY, "1", nx=True, ex=240)
        return bool(ok)
