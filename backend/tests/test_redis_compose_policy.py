from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "infrastructure" / "docker-compose.yml"


def test_redis_uses_noeviction():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "noeviction" in text
    assert "allkeys-lru" not in text
