"""discovery 终认线索 Redis 队列(discovery-spec §4.4 / §6.4)。

键：
- crucible:lead_verify:{task_id} — LIST，元素 JSON {lead_run_id, group_id, run_id}
- crucible:lead_inflight:{task_id} — SET，进行中的 lead_run_id

连接 settings.redis_url（db0），禁止写 Celery broker db1。
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

QUEUE_PREFIX = "crucible:lead_verify:"
INFLIGHT_PREFIX = "crucible:lead_inflight:"

_injected: Any = None


def set_redis_client(client: Any) -> None:
    """测试注入；传 None 恢复每调用新建连接。"""
    global _injected
    _injected = client


def queue_key(task_id: str) -> str:
    return f"{QUEUE_PREFIX}{task_id}"


def inflight_key(task_id: str) -> str:
    return f"{INFLIGHT_PREFIX}{task_id}"


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


async def enqueue_leads(task_id: str, items: list[dict[str, str]]) -> int:
    """LPUSH 全部线索；返回入队条数。空列表 no-op。"""
    if not items:
        return 0
    payloads = [json.dumps(it, ensure_ascii=False) for it in items]
    async with _redis() as r:
        await r.lpush(queue_key(task_id), *payloads)
    return len(payloads)


async def claim_lead(task_id: str) -> dict[str, str] | None:
    """RPOP 一条并记入 inflight；空队返回 None。"""
    async with _redis() as r:
        raw = await r.rpop(queue_key(task_id))
        if not raw:
            return None
        item = json.loads(raw)
        lead_run_id = item.get("lead_run_id")
        if lead_run_id:
            await r.sadd(inflight_key(task_id), lead_run_id)
        return item


async def complete_lead(task_id: str, lead_run_id: str) -> None:
    async with _redis() as r:
        await r.srem(inflight_key(task_id), lead_run_id)


async def requeue_lead(task_id: str, item: dict[str, str]) -> None:
    """失败可重试：从 inflight 移除并 LPUSH 回队。"""
    async with _redis() as r:
        lead_run_id = item.get("lead_run_id")
        if lead_run_id:
            await r.srem(inflight_key(task_id), lead_run_id)
        await r.lpush(queue_key(task_id), json.dumps(item, ensure_ascii=False))


async def queue_depth(task_id: str) -> int:
    async with _redis() as r:
        return int(await r.llen(queue_key(task_id)) or 0)


async def queued_lead_ids(task_id: str) -> set[str]:
    """当前队列中全部 lead_run_id（dispatch 补偿 / 孤儿回收用）。"""
    async with _redis() as r:
        raw_items = await r.lrange(queue_key(task_id), 0, -1)
    ids: set[str] = set()
    for raw in raw_items or []:
        try:
            ids.add(str(json.loads(raw).get("lead_run_id")))
        except (ValueError, TypeError):
            continue
    return ids


async def inflight_lead_ids(task_id: str) -> set[str]:
    async with _redis() as r:
        return {str(x) for x in (await r.smembers(inflight_key(task_id)) or [])}





async def inflight_count(task_id: str) -> int:
    async with _redis() as r:
        return int(await r.scard(inflight_key(task_id)) or 0)


async def is_drained(task_id: str) -> bool:
    async with _redis() as r:
        q = int(await r.llen(queue_key(task_id)) or 0)
        inflight = int(await r.scard(inflight_key(task_id)) or 0)
        return q == 0 and inflight == 0


async def clear_task_queue(task_id: str) -> None:
    async with _redis() as r:
        await r.delete(queue_key(task_id), inflight_key(task_id))
