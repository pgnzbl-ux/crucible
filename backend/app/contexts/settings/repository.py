from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import LlmProvider


class SettingsRepository:
    """LLM Provider 数据访问层"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_providers(self) -> list[LlmProvider]:
        result = await self.session.execute(
            select(LlmProvider).order_by(LlmProvider.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, provider_id: str) -> LlmProvider | None:
        result = await self.session.execute(
            select(LlmProvider).where(LlmProvider.id == provider_id)
        )
        return result.scalar_one_or_none()

    async def get_default(self) -> LlmProvider | None:
        """当前默认 Provider（is_default=true 且 enabled）"""
        result = await self.session.execute(
            select(LlmProvider)
            .where(LlmProvider.is_default.is_(True), LlmProvider.enabled.is_(True))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create(self, provider: LlmProvider) -> LlmProvider:
        self.session.add(provider)
        await self.session.flush()
        await self.session.refresh(provider)
        return provider

    async def clear_default(self) -> None:
        """清除所有默认标记（激活切换前调用）"""
        await self.session.execute(update(LlmProvider).values(is_default=False))

    async def delete(self, provider: LlmProvider) -> None:
        await self.session.delete(provider)
        await self.session.flush()
