"""创建/重试与 worker 共用的平台准入（LLM + agent-runner 镜像）。"""
from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.agent_runner import agent_runner_manager
from app.core.config import get_settings


async def require_runner_image() -> None:
    exists = await asyncio.to_thread(agent_runner_manager.image_exists)
    if not exists:
        image = get_settings().agent_runner_image
        raise ValueError(
            f"agent-runner 镜像不存在或 Docker 不可用: {image}（先 docker build）"
        )


async def require_platform_ready(session: AsyncSession) -> None:
    from app.contexts.settings.repository import SettingsRepository
    from app.contexts.settings.service import SettingsService

    await SettingsService(SettingsRepository(session)).require_ready_default_provider()
    await require_runner_image()
