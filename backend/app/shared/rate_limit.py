"""登录/注册速率限制：优先 Redis 固定窗口；不可用时回退进程内滑动窗口。"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock

logger = logging.getLogger(__name__)

_lock = Lock()
_hits: dict[str, list[float]] = defaultdict(list)
_redis_client = None
_redis_failed_at = 0.0
_REDIS_RETRY_SECONDS = 30.0


def _memory_check(key: str, *, limit: int, window_seconds: float) -> bool:
    now = time.monotonic()
    cutoff = now - window_seconds
    with _lock:
        # 顺带清扫过期桶，避免任意 email 枚举撑大内存
        stale = [k for k, ts in _hits.items() if not ts or ts[-1] < cutoff]
        for k in stale:
            del _hits[k]
        bucket = _hits[key]
        hits = [t for t in bucket if t >= cutoff]
        if len(hits) >= limit:
            _hits[key] = hits
            return False
        hits.append(now)
        _hits[key] = hits
        return True


def _get_redis():
    """懒连接；失败后短暂熔断，避免每请求打挂掉的 Redis。"""
    global _redis_client, _redis_failed_at
    now = time.monotonic()
    if _redis_client is not None:
        return _redis_client
    if now - _redis_failed_at < _REDIS_RETRY_SECONDS:
        return None
    try:
        import redis

        from app.core.config import get_settings

        client = redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        _redis_client = client
        return client
    except Exception:  # noqa: BLE001
        _redis_failed_at = now
        _redis_client = None
        logger.debug("rate_limit Redis 不可用，回退进程内窗口", exc_info=True)
        return None


# Lua 原子递增并设过期时间：首个请求原子设 TTL，杜绝网络异常致使键无 TTL 永久 429 (F-57)
_RATE_LIMIT_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""

def _redis_check(key: str, *, limit: int, window_seconds: float) -> bool | None:
    client = _get_redis()
    if client is None:
        return None
    rk = f"rl:{key}"
    try:
        count = client.eval(_RATE_LIMIT_LUA, 1, rk, max(1, int(window_seconds)))
        return int(count) <= limit
    except Exception:  # noqa: BLE001
        global _redis_client, _redis_failed_at
        _redis_client = None
        _redis_failed_at = time.monotonic()
        logger.debug("rate_limit Redis 操作失败，回退进程内窗口", exc_info=True)
        return None


def check_rate_limit(key: str, *, limit: int, window_seconds: float) -> bool:
    """未超限返回 True；超限返回 False。"""
    redis_result = _redis_check(key, limit=limit, window_seconds=window_seconds)
    if redis_result is not None:
        return redis_result
    return _memory_check(key, limit=limit, window_seconds=window_seconds)


def reset_rate_limits() -> None:
    """测试用：清空进程内桶并丢弃 Redis 客户端缓存。"""
    global _redis_client, _redis_failed_at
    with _lock:
        _hits.clear()
    _redis_client = None
    _redis_failed_at = 0.0
