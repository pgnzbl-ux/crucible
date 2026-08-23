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

    # socket 超时必须有：_enqueue 持有未提交行锁跨网络调 Redis，
    # 分区时主会话事务会无限悬挂（对齐 runner_slots 的 2s/5s）
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


async def enqueue_leads(task_id: str, items: list[dict[str, str]]) -> int:
    """LPUSH 全部线索；返回新入队条数。空列表 no-op。

    以 lead_run_id 维度 SET 去重：同一 run 重投双跑(dispatch 与 triage
    流式、或两个编排器并发)时，一条 LeadRun 只会有一份队列条目——
    RPOP 的原子性防不了同一 lead 的重复入队。完成/回收时清标记。
    不依赖 sadd 返回值（部分客户端/测试桩返回 None），改用 smembers 预查。
    """
    if not items:
        return 0
    async with _redis() as r:
        existing = {str(m) for m in (await r.smembers(enqueued_key(task_id)) or [])}
        fresh_ids: list[str] = []
        fresh_payloads: list[str] = []
        seen: set[str] = set()
        for it in items:
            lead_run_id = it.get("lead_run_id")
            if not lead_run_id or lead_run_id in seen:
                continue
            seen.add(lead_run_id)
            if lead_run_id in existing:
                continue  # 已在队列（或从未被 complete 清除）
            fresh_ids.append(lead_run_id)
            fresh_payloads.append(json.dumps(it, ensure_ascii=False))
        if fresh_payloads:
            await r.sadd(enqueued_key(task_id), *fresh_ids)
            await r.lpush(queue_key(task_id), *fresh_payloads)
    return len(fresh_payloads)


def enqueued_key(task_id: str) -> str:
    return f"crucible:lead:{task_id}:enqueued"


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
        # 清入队标记：失败重试/回收后的再次入队不受去重拦截
        await r.srem(enqueued_key(task_id), lead_run_id)


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
        await r.delete(
            queue_key(task_id), inflight_key(task_id), enqueued_key(task_id),
        )
