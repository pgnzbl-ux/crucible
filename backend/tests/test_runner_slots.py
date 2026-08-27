"""AI 容器与同靶场复现的跨进程槽位语义。"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import pytest


class _LeaseRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.slots: dict[str, dict[str, float]] = defaultdict(dict)

    async def mset(self, values):  # noqa: ANN001
        self.values.update(values)
        return True

    async def eval(self, script, key_count, *args):  # noqa: ANN001
        keys = args[:key_count]
        argv = args[key_count:]
        if "ZREMRANGEBYSCORE" in script:
            slots_key, limit_key = keys
            holder, now, lease_seconds, fallback = argv
            now_f = float(now)
            cutoff = now_f - float(lease_seconds)
            slots = self.slots[slots_key]
            for stale in [key for key, score in slots.items() if score <= cutoff]:
                slots.pop(stale, None)
            if holder in slots:
                slots[holder] = now_f
                return 1
            limit = int(self.values.get(limit_key, fallback))
            if len(slots) >= max(1, limit):
                return 0
            slots[holder] = now_f
            return 1

        slots_key = keys[0]
        holder, now = argv
        if holder in self.slots[slots_key]:
            self.slots[slots_key][holder] = float(now)
            return 1
        return 0

    async def zrem(self, slots_key: str, holder: str) -> int:
        return int(self.slots[slots_key].pop(holder, None) is not None)


@pytest.mark.asyncio
async def test_global_agent_runner_budget_caps_parallel_work():
    from app.contexts.agent.runner_slots import (
        agent_runner_slot,
        set_redis_client,
        set_runtime_limits,
    )

    redis = _LeaseRedis()
    set_redis_client(redis)
    await set_runtime_limits(agent_runners=2, reproduce_per_lab=1)
    active = 0
    peak = 0

    async def work(i: int) -> None:
        nonlocal active, peak
        async with agent_runner_slot(task_id=f"t{i}", node_key="audit"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(work(i) for i in range(6)))
    assert peak == 2


@pytest.mark.asyncio
async def test_same_lab_reproduction_has_its_own_budget():
    from app.contexts.agent.runner_slots import (
        reproduce_slot,
        set_redis_client,
        set_runtime_limits,
    )

    redis = _LeaseRedis()
    set_redis_client(redis)
    await set_runtime_limits(agent_runners=4, reproduce_per_lab=1)
    active = 0
    peak = 0

    async def work() -> None:
        nonlocal active, peak
        async with reproduce_slot("lab-1"):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(work() for _ in range(4)))
    assert peak == 1
    assert all(score <= time.time() for slots in redis.slots.values() for score in slots.values())


@pytest.mark.asyncio
async def test_wait_callback_is_emitted_once_when_budget_is_full():
    from app.contexts.agent.runner_slots import (
        agent_runner_slot,
        set_redis_client,
        set_runtime_limits,
    )

    redis = _LeaseRedis()
    set_redis_client(redis)
    await set_runtime_limits(agent_runners=1, reproduce_per_lab=1)
    waits: list[str] = []

    async def queued_work() -> None:
        async with agent_runner_slot(
            task_id="queued",
            node_key="audit",
            on_wait=lambda: waits.append("waiting"),
        ):
            return

    async with agent_runner_slot(task_id="running", node_key="audit"):
        queued = asyncio.create_task(queued_work())
        await asyncio.sleep(0.6)
        assert waits == ["waiting"]
    await queued
