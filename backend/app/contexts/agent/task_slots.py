"""平台验证任务运行槽（Redis SET + Lua）。

连接 settings.redis_url（db0），禁止写 Celery broker db1。
"""
from __future__ import annotations

from typing import Any

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

_client: Any = None


def set_redis_client(client: Any) -> None:
    """测试注入；传 None 恢复懒加载。"""
    global _client
    _client = client


async def _redis() -> Any:
    global _client
    if _client is None:
        import redis.asyncio as aioredis

        from app.core.config import get_settings

        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


async def try_acquire_slot(run_id: str, max_slots: int) -> bool:
    r = await _redis()
    capped = max(1, int(max_slots))
    result = await r.eval(_ACQUIRE_LUA, 1, SLOT_SET_KEY, run_id, str(capped))
    return int(result) == 1


async def release_slot(run_id: str) -> None:
    r = await _redis()
    await r.srem(SLOT_SET_KEY, run_id)


async def reconcile_slots(running_run_ids: set[str]) -> None:
    r = await _redis()
    current = set(await r.smembers(SLOT_SET_KEY) or [])
    wanted = set(running_run_ids)
    for extra in current - wanted:
        await r.srem(SLOT_SET_KEY, extra)
    for missing in wanted - current:
        await r.sadd(SLOT_SET_KEY, missing)


async def try_sweeper_lock() -> bool:
    r = await _redis()
    ok = await r.set(SWEEPER_LOCK_KEY, "1", nx=True, ex=240)
    return bool(ok)
