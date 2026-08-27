"""pytest 进程级覆盖：单元测试走 sqlite，不碰 .env 里的 PostgreSQL。

必须在任何 `app` 导入之前设置。环境变量优先于 `.env`，运行时 DATABASE_URL 仍只写在 `.env`。
"""

import os

import pytest

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
os.environ.setdefault("REDIS_CLUE_URL", "redis://localhost:6379/3")
os.environ.setdefault("S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("S3_ACCESS_KEY", "test-access-key")
os.environ.setdefault("S3_SECRET_KEY", "test-secret-key")
os.environ.setdefault("SCANNER_AUTO_INSTALL", "false")


class _ImmediateRunnerRedis:
    """AI runner 槽测试默认立即准入；具体配额算法由 runner_slots 专项测试覆盖。"""

    async def eval(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return 1

    async def mset(self, values):  # noqa: ANN001
        return True

    async def zrem(self, *args):  # noqa: ANN002
        return 1


@pytest.fixture(autouse=True)
def _inject_runner_slot_redis():
    from app.contexts.agent.runner_slots import set_redis_client

    set_redis_client(_ImmediateRunnerRedis())
    yield
    set_redis_client(None)
