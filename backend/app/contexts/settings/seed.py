"""
LLM Provider 种子迁移 — 环境变量 → DB（幂等）。

场景：老用户已在 .env 配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL，
升级后首次启动自动同步为一条默认 Provider，实现无缝迁移。
之后一切以 DB 为准（后台可改）。
"""

from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import encrypt_secret
from .models import LlmProvider

settings = get_settings()


async def seed_llm_provider_from_env(session: AsyncSession) -> bool:
    """环境变量有 LLM 配置且 DB 无 Provider 时，自动创建默认 Provider。

    返回是否执行了种子写入。
    """
    if not settings.llm_base_url or not settings.llm_api_key:
        return False

    count_result = await session.execute(select(func.count(LlmProvider.id)))
    if (count_result.scalar() or 0) > 0:
        return False  # DB 已有配置，不覆盖

    provider = LlmProvider(
        name="环境变量迁移（DeepSeek）",
        provider_type="deepseek" if "deepseek" in settings.llm_base_url else "custom",
        base_url=settings.llm_base_url,
        api_key_encrypted=encrypt_secret(settings.llm_api_key),
        model=settings.llm_model,
        timeout_ms=settings.llm_timeout_ms,
        enabled=True,
        is_default=True,
        extra="{}",
    )
    session.add(provider)
    await session.flush()
    return True
