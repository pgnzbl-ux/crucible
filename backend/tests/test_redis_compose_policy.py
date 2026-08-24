from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "infrastructure" / "docker-compose.yml"


def test_redis_uses_noeviction():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "noeviction" in text
    assert "allkeys-lru" not in text


def test_infra_compose_binds_loopback_and_requires_secrets():
    """F-01：端口仅回环；Redis requirepass；口令经环境变量注入。"""
    text = COMPOSE.read_text(encoding="utf-8")
    assert "127.0.0.1:${POSTGRES_PORT" in text or '127.0.0.1:${POSTGRES_PORT:-5433}:5432' in text
    assert "127.0.0.1:${REDIS_PORT" in text or "127.0.0.1:${REDIS_PORT:-6380}:6379" in text
    assert "127.0.0.1:${MINIO_API_PORT" in text
    assert "--requirepass" in text
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD" in text
    assert "MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD" in text
    # 不应再裸绑 0.0.0.0 / 无主机前缀的 "5433:5432"
    assert '"5433:5432"' not in text
    assert '"6380:6379"' not in text
    assert '"9000:9000"' not in text
