"""LLM Provider 的运行时单一契约。

ORM、Agent 容器、轻量 Messages 和连接测试都通过这里解释认证与高级参数，
避免各调用路径自行复制字段或同时发送多种认证凭据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from .models import (
    DEFAULT_LLM_EFFORT,
    DEFAULT_LLM_MAX_CONTEXT_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
)

AuthMode = Literal["api_key", "bearer"]


def default_auth_mode(provider_type: str | None) -> AuthMode:
    """官方 Anthropic 使用 X-Api-Key；DeepSeek/custom 保持历史 Bearer 行为。"""
    return "api_key" if provider_type == "anthropic" else "bearer"


def resolve_auth_mode(provider_type: str | None, auth_mode: str | None) -> AuthMode:
    resolved = auth_mode or default_auth_mode(provider_type)
    if resolved not in {"api_key", "bearer"}:
        raise ValueError(f"不支持的 Provider 认证方式: {resolved}")
    return cast(AuthMode, resolved)


def normalize_effort(value: str | None) -> str | None:
    """`auto` 是平台语义：不向 SDK/API 显式下发 effort。"""
    normalized = (value or DEFAULT_LLM_EFFORT).strip().lower()
    return None if normalized == "auto" else normalized


def anthropic_auth_headers(credential: str, auth_mode: AuthMode) -> dict[str, str]:
    if auth_mode == "api_key":
        return {"x-api-key": credential}
    return {"authorization": f"Bearer {credential}"}


def anthropic_auth_env(credential: str, auth_mode: AuthMode) -> dict[str, str]:
    if auth_mode == "api_key":
        return {"ANTHROPIC_API_KEY": credential}
    return {"ANTHROPIC_AUTH_TOKEN": credential}


@dataclass(frozen=True, slots=True)
class ProviderRuntimeConfig:
    id: str
    provider_type: str
    auth_mode: AuthMode
    base_url: str
    credential: str
    model: str
    timeout_ms: int
    temperature: float
    max_context_tokens: int
    effort: str

    @classmethod
    def from_provider(cls, provider) -> "ProviderRuntimeConfig":
        provider_type = str(getattr(provider, "provider_type", None) or "custom")
        return cls(
            id=str(getattr(provider, "id", "") or ""),
            provider_type=provider_type,
            auth_mode=resolve_auth_mode(provider_type, getattr(provider, "auth_mode", None)),
            base_url=str(getattr(provider, "base_url", "") or ""),
            credential=str(getattr(provider, "api_key_encrypted", "") or ""),
            model=str(getattr(provider, "model", "") or ""),
            timeout_ms=int(getattr(provider, "timeout_ms", None) or 120_000),
            temperature=float(
                DEFAULT_LLM_TEMPERATURE if getattr(provider, "temperature", None) is None else provider.temperature
            ),
            max_context_tokens=int(
                DEFAULT_LLM_MAX_CONTEXT_TOKENS
                if getattr(provider, "max_context_tokens", None) is None
                else provider.max_context_tokens
            ),
            effort=str(getattr(provider, "effort", None) or DEFAULT_LLM_EFFORT),
        )

    def auth_headers(self) -> dict[str, str]:
        return anthropic_auth_headers(self.credential, self.auth_mode)

    def agent_env(self) -> dict[str, str]:
        env = {
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_MODEL": self.model,
            "ANTHROPIC_SMALL_FAST_MODEL": self.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": self.model,
            "API_TIMEOUT_MS": str(self.timeout_ms),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            # 外层已是一次性 Docker；开 SCRUB=1 会强制内层 bwrap，在
            # no-new-privileges/默认 seccomp 下 Bash 探针常失败。凭据剥离改由
            # runner PreToolUse 对 Bash 包一层 env -u（见 run_one）。
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "0",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(self.max_context_tokens),
        }
        env.update(anthropic_auth_env(self.credential, self.auth_mode))
        effort = normalize_effort(self.effort)
        if effort is not None:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
            env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] = "1"
        return env
