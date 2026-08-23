"""轻量 LLM 网关 — worker 进程内直连 Anthropic 兼容 /v1/messages(discovery-spec §7)。

边界(discovery-spec §4.3/§8)：只消费已切文本；不接收仓库路径、不给工具、不执行代码。
role 仅 screening/final；hunting 是 P2 占位，解析到即抛配置错误。
Mock 开关 llm_gateway_enabled=False 返回固定判决，供链路联调。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

VALID_ROLES = ("screening", "final")

# 绝对路径剥离(§8 工位隔离)：切片只许相对路径，出现文件系统根路径一律替换
_ABS_PATH_RE = re.compile(r"(?<![\w.])/(?:tmp|home|workspace|opt|var|root|Users|mnt)/[^\s\"']+")


class LlmGatewayConfigError(RuntimeError):
    """角色非法或无可用 Provider。"""


@dataclass(frozen=True)
class LlmResult:
    text: str
    model: str | None
    provider_id: str | None
    usage: dict[str, int]


def scrub_paths(text: str) -> str:
    """输入边界：剥离文件系统绝对路径(保留文件名以维持可读性)。"""
    if not text:
        return text
    return _ABS_PATH_RE.sub(lambda m: "[path]" + m.group(0).rsplit("/", 1)[-1], text)


def assert_no_repo_paths(*texts: str) -> None:
    for t in texts:
        if t and _ABS_PATH_RE.search(t):
            raise LlmGatewayConfigError("轻量 runner 输入不得包含仓库/文件系统绝对路径")


async def _resolve_provider(session, role: str):
    """role 匹配 → is_default 兜底；hunting/未知角色直接失败。"""
    from sqlalchemy import select

    from app.contexts.settings.models import LlmProvider

    if role not in VALID_ROLES:
        raise LlmGatewayConfigError(
            f"非法模型角色: {role}（Phase 1 仅 {VALID_ROLES}；hunting 为 P2 占位）"
        )
    result = await session.execute(
        select(LlmProvider).where(LlmProvider.role == role, LlmProvider.is_default.isnot(True))
    )
    provider = result.scalars().first()
    if provider is None:
        result = await session.execute(
            select(LlmProvider).where(LlmProvider.is_default.is_(True))
        )
        provider = result.scalars().first()
    if provider is None:
        raise LlmGatewayConfigError("未配置任何 LLM Provider（含默认），无法轻量二审")
    return provider


def _mock_verdict_text(role: str) -> str:
    return json.dumps({
        "verdict": "need_more_context",
        "confidence": 0.3,
        "why": ["[Mock] llm_gateway_enabled=False 固定判决"],
        "evidence": [],
        "need": ["[Mock] 无"],
    }, ensure_ascii=False)


async def llm_complete(
    *,
    role: str,
    system: str,
    user: str,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    session=None,
    provider=None,
) -> LlmResult:
    """单次补全。session 与 provider 至少给一个(测试可直注 provider)。"""
    from app.core.config import get_settings

    settings = get_settings()
    system = scrub_paths(system or "")
    user = scrub_paths(user or "")

    if provider is None:
        if session is None:
            raise LlmGatewayConfigError("需要 session 或 provider")
        provider = await _resolve_provider(session, role)

    if not settings.llm_gateway_enabled:
        return LlmResult(
            text=_mock_verdict_text(role), model=provider.model,
            provider_id=provider.id, usage={"prompt_tokens": 0, "completion_tokens": 0},
        )

    import httpx

    url = provider.base_url.rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": provider.api_key_encrypted or "",
        "authorization": f"Bearer {provider.api_key_encrypted or ''}",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": provider.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    timeout_seconds = min((provider.timeout_ms or 120000) / 1000, 120)
    last_error: Exception | None = None
    for attempt in range(3):  # 网络错误指数退避重试 2 次
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(f"上游 {resp.status_code}", request=resp.request)
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
            blocks = data.get("content") or []
            text = "".join(
                b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
            )
            usage_raw = data.get("usage") or {}
            return LlmResult(
                text=text,
                model=data.get("model") or provider.model,
                provider_id=provider.id,
                usage={
                    "prompt_tokens": int(usage_raw.get("input_tokens") or 0),
                    "completion_tokens": int(usage_raw.get("output_tokens") or 0),
                },
            )
        except Exception as e:  # noqa: BLE001 — 4xx 直接失败，5xx/网络重试
            last_error = e
            if attempt < 2 and not isinstance(e, httpx.HTTPStatusError):
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            break
    raise LlmGatewayConfigError(f"LLM 网关调用失败({role}): {last_error}")


def parse_verdict_json(text: str) -> dict[str, Any]:
    """剥离 ```json 围栏后解析；失败抛 ValueError(调用方决定重问或降级)。"""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    # 容错：截取首个完整 JSON 对象
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)
