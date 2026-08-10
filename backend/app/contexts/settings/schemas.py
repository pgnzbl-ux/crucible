from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ── 请求 ──

class LlmProviderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    provider_type: str = Field("custom", pattern=r"^(deepseek|tencent|openai_compat|anthropic|custom)$")
    base_url: str = Field(..., min_length=5, max_length=512)
    api_key: str = Field("", max_length=2048, description="明文 API Key，服务端加密存储")
    model: str = Field(..., min_length=1, max_length=100)
    timeout_ms: int = Field(600000, ge=10_000, le=3_600_000)
    enabled: bool = True
    is_default: bool = False
    extra: dict = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return v.rstrip("/")


class LlmProviderUpdateRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    provider_type: str | None = Field(None, pattern=r"^(deepseek|tencent|openai_compat|anthropic|custom)$")
    base_url: str | None = Field(None, min_length=5, max_length=512)
    api_key: str | None = Field(None, max_length=2048, description="留空表示不修改")
    model: str | None = Field(None, min_length=1, max_length=100)
    timeout_ms: int | None = Field(None, ge=10_000, le=3_600_000)
    enabled: bool | None = None
    extra: dict | None = None


class LlmProviderTestRequest(BaseModel):
    """测试连接 — 可用已有 Provider 或临时参数"""
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


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
    enabled: bool
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
