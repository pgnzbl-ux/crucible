from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Credential, LlmProvider


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


class CredentialRepository:
    """任务级凭据数据访问层（P1-6 Credential Proxy）"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_by_owner(self, owner_id: str) -> list[Credential]:
        result = await self.session.execute(
            select(Credential)
            .where(Credential.owner_id == owner_id)
            .order_by(Credential.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, credential_id: str) -> Credential | None:
        result = await self.session.execute(
            select(Credential).where(Credential.id == credential_id)
        )
        return result.scalar_one_or_none()

    async def get_by_ids_for_owner(self, ids: list[str], owner_id: str) -> list[Credential]:
        """按 id 列表批量取凭据（校验 owner，任务注入时用）"""
        if not ids:
            return []
        result = await self.session.execute(
            select(Credential).where(Credential.id.in_(ids), Credential.owner_id == owner_id)
        )
        return list(result.scalars().all())

    async def create(self, credential: Credential) -> Credential:
        self.session.add(credential)
        await self.session.flush()
        await self.session.refresh(credential)
        return credential

    async def delete(self, credential: Credential) -> None:
        await self.session.delete(credential)
        await self.session.flush()
