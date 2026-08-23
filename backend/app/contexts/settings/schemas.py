from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.url_security import normalize_https_domain_url

_PROVIDER_TYPES = frozenset({"deepseek", "anthropic", "custom"})


def normalize_provider_type(value: str) -> str:
    """历史 openai_compat 实为 Anthropic 兼容端点，对外统一为 custom。"""
    if value == "openai_compat":
        return "custom"
    return value


# ── 请求 ──

class LlmProviderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider_type: str = Field("deepseek", pattern=r"^(deepseek|anthropic|custom)$")
    base_url: str = Field(..., min_length=5, max_length=512)
    api_key: str = Field("", max_length=2048, description="明文 API Key，服务端加密存储")
    model: str = Field(..., min_length=1, max_length=100)
    timeout_ms: int = Field(600000, ge=10_000, le=3_600_000)
    is_default: bool = False
    extra: dict = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return normalize_https_domain_url(v)


class LlmProviderUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    provider_type: str | None = Field(None, pattern=r"^(deepseek|anthropic|custom)$")
    base_url: str | None = Field(None, min_length=5, max_length=512)
    api_key: str | None = Field(None, max_length=2048, description="留空表示不修改")
    model: str | None = Field(None, min_length=1, max_length=100)
    timeout_ms: int | None = Field(None, ge=10_000, le=3_600_000)
    extra: dict | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        return normalize_https_domain_url(v) if v is not None else None


class LlmProviderTestRequest(BaseModel):
    """测试连接 — 可用已有 Provider 或临时参数"""
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, v: str | None) -> str | None:
        return normalize_https_domain_url(v) if v is not None else None


# ── 响应 ──

class LlmProviderResponse(BaseModel):
    id: str
    name: str
    provider_type: str
    base_url: str
    api_key_masked: str = ""
    has_api_key: bool = False
    model: str
    timeout_ms: int
    is_default: bool
    created_at: datetime
    updated_at: datetime


class LlmProviderListResponse(BaseModel):
    items: list[LlmProviderResponse]
    total: int


class LlmProviderTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: int | None = None
    model: str | None = None


# ── Credential（任务级凭据，P1-6） ──

import re

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,62}$")
_SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class CredentialCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    kind: str = Field("env_var", pattern=r"^(env_var|file)$")
    target: str = Field(..., min_length=1, max_length=255, description="env_var→环境变量名(大写下划线) / file→文件名")
    secret: str = Field(..., min_length=1, max_length=8192, description="明文凭据，明文存储（响应层掩码）")
    description: str | None = Field(None, max_length=500)

    @field_validator("target")
    @classmethod
    def _validate_target(cls, v: str) -> str:
        # 校验在 kind 之后，但 Pydantic 不保证字段顺序，这里宽松校验 + service 侧严格校验
        return v.strip()

    @model_validator(mode="after")
    def _validate_kind_target(self) -> "CredentialCreateRequest":
        from app.core.credential_proxy import is_reserved_env_target

        if self.kind == "env_var" and not _ENV_NAME_RE.match(self.target):
            raise ValueError("env_var 类型的 target 必须是大写下划线环境变量名（如 DB_PASSWORD）")
        if self.kind == "env_var" and is_reserved_env_target(self.target):
            raise ValueError("该环境变量名由平台保留，不能作为任务凭据")
        if self.kind == "file" and not _SAFE_FILE_RE.match(self.target):
            raise ValueError("file 类型的 target 必须是安全文件名（字母数字._-，禁止路径分隔符）")
        return self


class CredentialUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    secret: str | None = Field(None, min_length=1, max_length=8192, description="留空表示不修改")
    description: str | None = Field(None, max_length=500)


class CredentialResponse(BaseModel):
    id: str
    name: str
    kind: str
    target: str
    secret_masked: str = ""
    has_secret: bool = False
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class CredentialListResponse(BaseModel):
    items: list[CredentialResponse]
    total: int


class RuntimeSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_concurrent_tasks: int | None = Field(None, ge=1)
    max_concurrent_agent_runners: int | None = Field(None, ge=1)
    lead_verify_per_task: int | None = Field(None, ge=1)
    reproduce_per_lab: int | None = Field(None, ge=1)
    task_token_budget: int | None = Field(None, ge=0)

    @model_validator(mode="after")
    def _validate_runtime_budget(self) -> "RuntimeSettingsUpdateRequest":
        from app.core.config import get_settings

        allowed = get_settings().agent_runner_concurrency_limit
        values = self.model_dump(exclude_none=True)
        if not values:
            raise ValueError("至少提交一项运行时设置")
        for field_name, value in values.items():
            if value > allowed:
                raise ValueError(f"{field_name} 不能超过 {allowed}")
        if (
            self.max_concurrent_agent_runners is not None
            and self.lead_verify_per_task is not None
            and self.lead_verify_per_task > self.max_concurrent_agent_runners
        ):
            raise ValueError("单任务线索终认并发不能超过全局 AI 容器并发")
        if (
            self.lead_verify_per_task is not None
            and self.reproduce_per_lab is not None
            and self.reproduce_per_lab > self.lead_verify_per_task
        ):
            raise ValueError("同靶场复现并发不能超过单任务线索终认并发")
        return self


class RuntimeSettingsResponse(BaseModel):
    max_concurrent_tasks: int
    max_concurrent_agent_runners: int
    lead_verify_per_task: int
    reproduce_per_lab: int
    task_token_budget: int = 0
    max_allowed: int
    agent_runner_max_allowed: int
    lead_verify_max_allowed: int
    reproduce_max_allowed: int
    worker_pool: Literal["prefork"]
