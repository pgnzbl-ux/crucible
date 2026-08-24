"""速率限制：进程内窗口清扫 + Redis 优先。"""
from app.shared.rate_limit import check_rate_limit, reset_rate_limits


def test_memory_rate_limit_blocks_after_limit(monkeypatch):
    reset_rate_limits()
    monkeypatch.setattr("app.shared.rate_limit._get_redis", lambda: None)

    key = "login:1.2.3.4:user@x"
    for _ in range(3):
        assert check_rate_limit(key, limit=3, window_seconds=60) is True
    assert check_rate_limit(key, limit=3, window_seconds=60) is False


def test_memory_rate_limit_prunes_stale_keys(monkeypatch):
    import app.shared.rate_limit as rl

    reset_rate_limits()
    monkeypatch.setattr(rl, "_get_redis", lambda: None)
    with rl._lock:
        rl._hits["old"] = [0.0]  # 远早于窗口
    assert check_rate_limit("new", limit=5, window_seconds=60) is True
    with rl._lock:
        assert "old" not in rl._hits
