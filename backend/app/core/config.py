from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Crucible 全局配置 — 全部通过环境变量注入，无硬编码默认值"""

    app_name: str = "Crucible API"
    app_version: str = "0.3.0"
    environment: str = "development"
    debug: bool = False

    # 数据库 — 开发默认 SQLite，生产必须 PostgreSQL
    database_url: str = "sqlite+aiosqlite:///./crucible.db"

    # Redis — Celery broker + 事件总线 + 缓存
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # JWT 认证
    auth_secret: str = ""
    auth_algorithm: str = "HS256"
    auth_token_expire_minutes: int = 480  # 8 小时
    admin_email: str = "admin@crucible.local"
    admin_password: str = ""

    # 敏感配置加密（Fernet）— 用于加密落库的 API Key 等凭据
    settings_encrypt_key: str = ""  # 生产必须设置 32 字节 base64 Fernet key

    # Claude Agent SDK — Python SDK 接入第三方 Anthropic 兼容端点（DeepSeek 等）
    # 凭据通过 docker run --env 注入 agent-runner 容器，容器销毁 env 消失（零落盘）
    claude_agent_sdk_enabled: bool = False
    claude_sdk_max_turns: int = 180

    # LLM Provider（Anthropic 兼容 API — DeepSeek / 腾讯云等）
    llm_base_url: str = ""                 # 如 https://api.deepseek.com/anthropic
    llm_api_key: str = ""                  # DeepSeek API Key（Claude Code 用 ANTHROPIC_AUTH_TOKEN 认证）
    llm_model: str = "deepseek-v4-flash"   # deepseek-v4-flash | deepseek-v4-pro | deepseek-v3.2 等
    llm_timeout_ms: int = 600000           # API_TIMEOUT_MS，防止长输出触发 Claude Code 超时
    llm_disable_nonessential_traffic: bool = True  # CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC

    # Agent Runner 容器（专用镜像，与代码层物理隔离，凭据零落盘）
    agent_runner_image: str = "crucible-agent-runner:base"
    agent_runner_cpu_limit: float = 1.0
    agent_runner_memory_limit: str = "1g"
    agent_runner_network: str = "crucible-sandbox-net"  # 复用现有专用网络（sandbox 拆除后仅供此用）
    agent_runner_workdir_base: str = "/tmp/crucible/audit"
    agent_runner_concurrency_limit: int = 4
    agent_runner_timeout_seconds: int = 1800  # Celery task_time_limit / agent-runner 容器总超时

    # CORS
    cors_origins: str = "http://localhost:5173,http://localhost:4173,http://localhost:3000"

    # 对象存储 (MinIO / S3)
    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "crucible-artifacts"
    s3_secure: bool = False

    # Sentry
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
