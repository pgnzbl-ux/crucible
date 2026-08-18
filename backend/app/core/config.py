from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def read_app_version() -> str:
    """产品版本唯一入口：backend/pyproject.toml [project].version。"""
    import tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


class Settings(BaseSettings):
    """平台配置的类型入口，不是第二份默认值清单。

    分层（禁止同一事实抄三份）：
    - `.env`：基础设施连接与密钥（DATABASE_URL / REDIS_* / S3_* / AUTH_SECRET）。
      这些字段在本类里**没有代码默认值**。pytest 用环境变量覆盖 DATABASE_URL 为 sqlite。
    - 本类默认值：行为开关与限额（environment / debug / SDK / runner 资源）。
    - 产品版本：`backend/pyproject.toml`，经 `read_app_version()` 读取，禁止在本文件抄号。
    - 代码常量：MinIO 桶与 kind（`shared/object_store.py`）；LLM 凭据禁止进 Settings。
    - 后台 DB：LLM Provider。
    - `alembic.ini` 不保存真实库地址，由 `alembic/env.py` 从本类注入。
    """

    app_name: str = "Crucible API"
    environment: str = "development"
    debug: bool = False

    database_url: str
    redis_url: str
    celery_broker_url: str
    celery_result_backend: str

    auth_secret: str = ""
    auth_algorithm: str = "HS256"
    auth_token_expire_minutes: int = 480

    settings_encrypt_key: str = ""

    claude_agent_sdk_enabled: bool = False
    claude_sdk_max_turns: int = 180

    agent_runner_image: str = "crucible-agent-runner:base"
    agent_runner_cpu_limit: float = 1.0
    agent_runner_memory_limit: str = "1g"
    agent_runner_network: str = "crucible-sandbox-net"
    agent_runner_workdir_base: str = "/tmp/crucible/audit"
    agent_runner_concurrency_limit: int = 4
    agent_runner_timeout_seconds: int = 1800

    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://localhost:3000"

    s3_endpoint: str
    s3_access_key: str
    s3_secret_key: str
    s3_secure: bool = False

    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _enforce_production_security(self) -> "Settings":
        if self.environment == "production":
            if not self.auth_secret:
                raise ValueError("生产环境必须设置 AUTH_SECRET")
            if "sqlite" in self.database_url:
                raise ValueError("生产环境禁止使用 SQLite，必须使用 PostgreSQL")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def app_version(self) -> str:
        return read_app_version()


@lru_cache
def get_settings() -> Settings:
    return Settings()
