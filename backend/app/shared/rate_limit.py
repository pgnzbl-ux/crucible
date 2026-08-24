"""登录/注册简单速率限制（进程内滑动窗口；多 worker 时各算各的，Nginx 仍应配 limit_req）。"""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock

_lock = Lock()
_hits: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(key: str, *, limit: int, window_seconds: float) -> bool:
    """未超限返回 True；超限返回 False。"""
    now = time.monotonic()
    cutoff = now - window_seconds
    with _lock:
        bucket = _hits[key]
        hits = [t for t in bucket if t >= cutoff]
        if len(hits) >= limit:
            _hits[key] = hits
            return False
        hits.append(now)
        _hits[key] = hits
        return True


def reset_rate_limits() -> None:
    """测试用。"""
    with _lock:
        _hits.clear()
