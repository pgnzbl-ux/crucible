"""运行槽：Lua 抢槽 / 释放 / 调和。"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.contexts.agent.task_slots import (
    SLOT_SET_KEY,
    set_redis_client,
    try_acquire_slot,
    release_slot,
    reconcile_slots,
    try_sweeper_lock,
)


class FakeRedis:
    def __init__(self) -> None:
        self.sets: dict[str, set[str]] = defaultdict(set)
        self.kv: dict[str, str] = {}

    async def eval(self, _script, _numkeys, *args):
        key, run_id, max_raw = args[0], args[1], args[2]
        max_slots = int(max_raw)
        if max_slots < 1:
            max_slots = 1
        members = self.sets[key]
        if run_id in members:
            return 1
        if len(members) >= max_slots:
            return 0
        members.add(run_id)
        return 1

    async def srem(self, key, member):
        self.sets[key].discard(member)

    async def smembers(self, key):
        return set(self.sets[key])

    async def sadd(self, key, member):
        self.sets[key].add(member)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return False
        self.kv[key] = value
        return True


@pytest.fixture
def fake_redis():
    client = FakeRedis()
    set_redis_client(client)
    yield client
    set_redis_client(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "max_slots,existing,run_id,ok,final",
    [
        (1, set(), "r1", True, {"r1"}),
        (1, {"r1"}, "r2", False, {"r1"}),
        (1, {"r1"}, "r1", True, {"r1"}),
        (2, {"r1"}, "r2", True, {"r1", "r2"}),
    ],
)
async def test_try_acquire_slot_table(fake_redis, max_slots, existing, run_id, ok, final):
    fake_redis.sets[SLOT_SET_KEY] = set(existing)
    got = await try_acquire_slot(run_id, max_slots)
    assert got is ok
    assert fake_redis.sets[SLOT_SET_KEY] == final


@pytest.mark.asyncio
async def test_reconcile_drops_stale_and_adds_missing(fake_redis):
    fake_redis.sets[SLOT_SET_KEY] = {"r1", "r2"}
    await reconcile_slots({"r2", "r3"})
    assert fake_redis.sets[SLOT_SET_KEY] == {"r2", "r3"}


@pytest.mark.asyncio
async def test_release_slot(fake_redis):
    fake_redis.sets[SLOT_SET_KEY] = {"r1", "r2"}
    await release_slot("r1")
    assert fake_redis.sets[SLOT_SET_KEY] == {"r2"}


@pytest.mark.asyncio
async def test_sweeper_lock_nx(fake_redis):
    assert await try_sweeper_lock() is True
    assert await try_sweeper_lock() is False


def test_acquire_survives_successive_asyncio_run():
    """Celery 每个任务 asyncio.run 一次；进程级缓存的 Redis 客户端会绑死已关闭的 loop。"""
    import asyncio
    from unittest.mock import patch

    from app.contexts.agent import task_slots as slots

    slots.set_redis_client(None)

    class LoopBoundRedis:
        def __init__(self) -> None:
            self.loop = asyncio.get_running_loop()

        async def eval(self, *_args, **_kwargs):
            if self.loop.is_closed() or asyncio.get_running_loop() is not self.loop:
                raise RuntimeError("Event loop is closed")
            return 1

        async def aclose(self) -> None:
            return None

    created: list[LoopBoundRedis] = []

    def from_url(*_args, **_kwargs):
        client = LoopBoundRedis()
        created.append(client)
        return client

    try:
        with patch("redis.asyncio.from_url", from_url):

            async def acquire() -> None:
                assert await try_acquire_slot("r1", 1) is True

            asyncio.run(acquire())
            asyncio.run(acquire())
    finally:
        slots.set_redis_client(None)

    assert len(created) >= 2

