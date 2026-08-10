"""
事件总线抽象层。

当前实现：Redis Pub/Sub（与 Celery 共用 Redis，零额外基础设施）
未来可替换为：NATS / Kafka / RabbitMQ
"""

import json
import time
import uuid
from typing import Any

import redis.asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()


class Event:
    """标准化事件结构"""

    def __init__(
        self,
        event_type: str,
        aggregate_id: str,
        aggregate_type: str,
        payload: dict[str, Any],
        *,
        actor: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ):
        self.event_id = f"evt_{uuid.uuid4().hex[:12]}"
        self.event_type = event_type
        self.aggregate_id = aggregate_id
        self.aggregate_type = aggregate_type
        self.timestamp = time.time()
        self.actor = actor or {}
        self.payload = payload
        self.correlation_id = correlation_id or ""

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "aggregate_id": self.aggregate_id,
            "aggregate_type": self.aggregate_type,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "payload": self.payload,
            "correlation_id": self.correlation_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class EventBus:
    """Redis Pub/Sub 事件总线"""

    def __init__(self, redis_url: str | None = None):
        self._redis_url = redis_url or settings.redis_url
        self._redis: aioredis.Redis | None = None

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def publish(self, channel: str, event: Event) -> int:
        """发布事件到指定频道，返回订阅者数量"""
        r = await self._get_redis()
        return await r.publish(channel, event.to_json())

    async def publish_domain_event(self, event: Event) -> int:
        """发布领域事件 — 频道规则: domain.{aggregate_type}.{event_type}"""
        channel = f"domain.{event.aggregate_type}.{event.event_type}"
        return await self.publish(channel, event)

    async def subscribe(self, channel: str):
        """订阅频道 — 返回异步迭代器"""
        r = await self._get_redis()
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)
        return pubsub

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()
            self._redis = None


# 全局事件总线实例
event_bus = EventBus()
