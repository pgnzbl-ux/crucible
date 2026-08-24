"""
LLM Provider 管理服务。

职责：
- CRUD(API key 明文落库,列表掩码回显)
- 默认 Provider 激活（全局唯一 is_default，即当前启用项）
- 测试连接（真实调用 Anthropic 兼容 /v1/messages）
- 运行时配置解析（Agent 任务从 DB 取默认 Provider → 环境变量注入沙箱）
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
from sqlalchemy.exc import IntegrityError

# 经模块属性访问而非导入期绑定：若本模块在被 patch 的
# get_settings 生效期间首次导入，顶层 from-import 会把 Mock 永久
# 捕获进本模块（测试顺序污染的根源）
from app.core import config as _core_config
from app.core.crypto import mask_secret
from app.core.url_security import validate_public_https_url

from .models import (
    DEFAULT_LLM_EFFORT,
    DEFAULT_LLM_MAX_CONTEXT_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
    Credential,
    LlmProvider,
    PlatformSetting,
)
from .repository import CredentialRepository, SettingsRepository
from .schemas import (
    CredentialCreateRequest,
    CredentialResponse,
    CredentialUpdateRequest,
    LlmProviderCreateRequest,
    LlmProviderResponse,
    LlmProviderTestResult,
    LlmProviderUpdateRequest,
    RuntimeSettingsResponse,
    RuntimeSettingsUpdateRequest,
    normalize_provider_type,
)

logger = logging.getLogger(__name__)


def to_response(provider: LlmProvider, plain_key: str = "") -> LlmProviderResponse:
    """ORM → 响应模型（api_key 掩码）"""
    if not plain_key:
        plain_key = provider.api_key_encrypted
    return LlmProviderResponse(
        id=provider.id,
        name=provider.name,
        provider_type=normalize_provider_type(provider.provider_type),
        base_url=provider.base_url,
        api_key_masked=mask_secret(plain_key),
        has_api_key=bool(plain_key),
        model=provider.model,
        timeout_ms=provider.timeout_ms,
        temperature=(
            DEFAULT_LLM_TEMPERATURE
            if provider.temperature is None
            else float(provider.temperature)
        ),
        max_context_tokens=(
            DEFAULT_LLM_MAX_CONTEXT_TOKENS
            if provider.max_context_tokens is None
            else int(provider.max_context_tokens)
        ),
        effort=provider.effort or DEFAULT_LLM_EFFORT,
        is_default=provider.is_default,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def worker_pool_hint() -> str:
    return "prefork"


class SettingsService:
    def __init__(self, repo: SettingsRepository):
        self.repo = repo

    # ── CRUD ──

    async def list_providers(self) -> tuple[list[LlmProviderResponse], int]:
        providers = await self.repo.list_providers()
        return [to_response(p) for p in providers], len(providers)

    async def create_provider(self, request: LlmProviderCreateRequest) -> LlmProviderResponse:
        base_url = await validate_public_https_url(request.base_url)
        existing_default = await self.repo.get_default()
        make_default = request.is_default or existing_default is None
        if make_default:
            await self.repo.clear_default()
        provider = LlmProvider(
            name=request.name,
            provider_type=request.provider_type,
            base_url=base_url,
            api_key_encrypted=request.api_key,
            model=request.model,
            timeout_ms=request.timeout_ms,
            temperature=request.temperature,
            max_context_tokens=request.max_context_tokens,
            effort=request.effort,
            is_default=make_default,
            extra=json.dumps(request.extra, ensure_ascii=False),
        )
        provider = await self.repo.create(provider)
        return to_response(provider, request.api_key)

    async def update_provider(
        self, provider_id: str, request: LlmProviderUpdateRequest
    ) -> LlmProviderResponse | None:
        provider = await self.repo.get_by_id(provider_id)
        if not provider:
            return None
        updates = request.model_dump(exclude_unset=True, exclude={"api_key"})
        if request.base_url is not None:
            updates["base_url"] = await validate_public_https_url(request.base_url)
        for field, value in updates.items():
            if field == "extra" and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            setattr(provider, field, value)
        if request.api_key:
            provider.api_key_encrypted = request.api_key
        await self.repo.session.flush()
        return to_response(provider, request.api_key or "")

    async def delete_provider(self, provider_id: str) -> bool:
        provider = await self.repo.get_by_id(provider_id)
        if not provider:
            return False
        await self.repo.delete(provider)
        return True

    async def activate_provider(self, provider_id: str) -> LlmProviderResponse | None:
        """设为默认（清除其它默认标记）"""
        provider = await self.repo.get_by_id(provider_id)
        if not provider:
            return None
        await self.repo.clear_default()
        provider.is_default = True
        await self.repo.session.flush()
        return to_response(provider)

    async def get_default_provider(self) -> LlmProvider | None:
        return await self.repo.get_default()

    async def require_ready_default_provider(self) -> LlmProvider:
        """创建/重试任务前的准入：必须有已激活且带 API Key 的默认 Provider。"""
        provider = await self.get_default_provider()
        if provider is None:
            raise ValueError(
                "未配置默认 LLM Provider，请到「设置」配置并激活后再创建或重试任务"
            )
        if not (provider.api_key_encrypted or "").strip():
            raise ValueError(
                "默认 LLM Provider 未配置 API Key，请到「设置」补全后再创建或重试任务"
            )
        return provider

    async def _get_or_create_platform_setting(self) -> PlatformSetting:
        row = await self.repo.get_platform_setting()
        if row is not None:
            return row
        hard_cap = _core_config.get_settings().agent_runner_concurrency_limit
        row = PlatformSetting(
            singleton_key="default",
            max_concurrent_tasks=1,
            max_concurrent_agent_runners=hard_cap,
            lead_verify_per_task=min(_core_config.get_settings().lead_verify_per_task, hard_cap),
            reproduce_per_lab=1, task_token_budget=0,
        )
        try:
            async with self.repo.session.begin_nested():
                return await self.repo.add_platform_setting(row)
        except IntegrityError:
            found = await self.repo.get_platform_setting()
            if found is None:
                raise
            return found

    def _runtime_response(self, row: PlatformSetting) -> RuntimeSettingsResponse:
        hard_cap = _core_config.get_settings().agent_runner_concurrency_limit
        # 旧数据或部署硬顶下调后，读取即收敛到一组真正可执行的预算。
        row.max_concurrent_tasks = min(max(1, row.max_concurrent_tasks), hard_cap)
        row.max_concurrent_agent_runners = min(
            max(1, row.max_concurrent_agent_runners), hard_cap
        )
        row.lead_verify_per_task = min(
            max(1, row.lead_verify_per_task), row.max_concurrent_agent_runners
        )
        row.reproduce_per_lab = min(
            max(1, row.reproduce_per_lab), row.lead_verify_per_task
        )
        return RuntimeSettingsResponse(
            max_concurrent_tasks=row.max_concurrent_tasks,
            max_concurrent_agent_runners=row.max_concurrent_agent_runners,
            lead_verify_per_task=row.lead_verify_per_task,
            reproduce_per_lab=row.reproduce_per_lab,
            task_token_budget=row.task_token_budget,
            max_allowed=hard_cap,
            agent_runner_max_allowed=hard_cap,
            lead_verify_max_allowed=hard_cap,
            reproduce_max_allowed=hard_cap,
            worker_pool=worker_pool_hint(),
        )

    async def _sync_scheduler_limits(self, runtime: RuntimeSettingsResponse) -> None:
        """把 DB 单一真相同步到 Redis 调度原语；保存必须保证实际生效。"""
        try:
            from app.contexts.agent.runner_slots import set_runtime_limits

            await asyncio.wait_for(
                set_runtime_limits(
                    agent_runners=runtime.max_concurrent_agent_runners,
                    reproduce_per_lab=runtime.reproduce_per_lab,
                ),
                timeout=1.0,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("运行时并发设置同步 Redis 失败", exc_info=True)
            raise RuntimeError("Redis 调度器不可用，并发设置未保存") from exc

    async def get_runtime_settings(self) -> RuntimeSettingsResponse:
        row = await self._get_or_create_platform_setting()
        response = self._runtime_response(row)
        await self.repo.session.flush()
        return response

    async def update_runtime_settings(
        self, request: RuntimeSettingsUpdateRequest
    ) -> RuntimeSettingsResponse:
        row = await self._get_or_create_platform_setting()
        updates = request.model_dump(exclude_none=True)
        merged = {
            "max_concurrent_tasks": row.max_concurrent_tasks,
            "max_concurrent_agent_runners": row.max_concurrent_agent_runners,
            "lead_verify_per_task": row.lead_verify_per_task,
            "reproduce_per_lab": row.reproduce_per_lab,
            "task_token_budget": row.task_token_budget,
            **updates,
        }
        if merged["lead_verify_per_task"] > merged["max_concurrent_agent_runners"]:
            raise ValueError("单任务线索终认并发不能超过全局 AI 容器并发")
        if merged["reproduce_per_lab"] > merged["lead_verify_per_task"]:
            raise ValueError("同靶场复现并发不能超过单任务线索终认并发")
        for field_name, value in merged.items():
            setattr(row, field_name, value)
        await self.repo.session.flush()
        response = self._runtime_response(row)
        await self._sync_scheduler_limits(response)
        return response

    # ── 测试连接 ──

    async def test_connection(
        self,
        *,
        provider_id: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        effort: str | None = None,
    ) -> LlmProviderTestResult:
        """真实调用 Anthropic 兼容 /v1/messages 验证凭据与模型可用性"""
        from app.contexts.settings.models import (
            DEFAULT_LLM_EFFORT,
            DEFAULT_LLM_TEMPERATURE,
        )

        resolved_url = base_url
        resolved_key = api_key
        resolved_model = model
        resolved_temperature = temperature
        resolved_effort = effort

        if provider_id:
            provider = await self.repo.get_by_id(provider_id)
            if provider:
                resolved_url = resolved_url or provider.base_url
                resolved_key = resolved_key or provider.api_key_encrypted
                resolved_model = resolved_model or provider.model
                if resolved_temperature is None:
                    resolved_temperature = provider.temperature
                if resolved_effort is None:
                    resolved_effort = provider.effort

        if resolved_temperature is None:
            resolved_temperature = DEFAULT_LLM_TEMPERATURE
        if resolved_effort is None:
            resolved_effort = DEFAULT_LLM_EFFORT

        if not resolved_url or not resolved_key or not resolved_model:
            return LlmProviderTestResult(ok=False, message="缺少 base_url / api_key / model 参数")

        try:
            resolved_url = await validate_public_https_url(resolved_url)
        except ValueError as exc:
            return LlmProviderTestResult(ok=False, message=str(exc))

        endpoint = f"{resolved_url}/v1/messages"
        started = time.time()
        payload = {
            "model": resolved_model,
            "max_tokens": 8,
            "temperature": resolved_temperature,
            "messages": [{"role": "user", "content": "ping"}],
            "output_config": {"effort": resolved_effort},
        }
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                resp = await client.post(
                    endpoint,
                    headers={
                        "x-api-key": resolved_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
            latency = int((time.time() - started) * 1000)
            if resp.status_code == 200:
                return LlmProviderTestResult(
                    ok=True, message=f"连接成功（{latency}ms）", latency_ms=latency, model=resolved_model
                )
            body = resp.text[:200]
            return LlmProviderTestResult(
                ok=False, message=f"HTTP {resp.status_code}: {body}", latency_ms=latency
            )
        except httpx.HTTPError as e:
            return LlmProviderTestResult(ok=False, message=f"网络错误: {e}")
        except Exception as e:  # noqa: BLE001
            return LlmProviderTestResult(ok=False, message=f"异常: {e}")

    # ── 运行时配置（Agent 调用） ──

    def build_env_from_provider(self, provider: LlmProvider) -> dict[str, str]:
        """Provider → 沙箱环境变量（凭据零落盘）"""
        max_context = (
            DEFAULT_LLM_MAX_CONTEXT_TOKENS
            if provider.max_context_tokens is None
            else int(provider.max_context_tokens)
        )
        effort = provider.effort or DEFAULT_LLM_EFFORT
        return {
            "ANTHROPIC_BASE_URL": provider.base_url,
            "ANTHROPIC_AUTH_TOKEN": provider.api_key_encrypted,
            "ANTHROPIC_API_KEY": provider.api_key_encrypted,
            "ANTHROPIC_MODEL": provider.model,
            "ANTHROPIC_SMALL_FAST_MODEL": provider.model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": provider.model,
            "API_TIMEOUT_MS": str(provider.timeout_ms),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(max_context),
            "CLAUDE_CODE_EFFORT_LEVEL": effort,
            "CLAUDE_CODE_ALWAYS_ENABLE_EFFORT": "1",
        }

    # ── Credential CRUD（P1-6 Credential Proxy） ──

    async def list_credentials(self, owner_id: str) -> tuple[list[CredentialResponse], int]:
        repo = CredentialRepository(self.repo.session)
        creds = await repo.list_by_owner(owner_id)
        return [_credential_to_response(c) for c in creds], len(creds)

    async def create_credential(
        self, owner_id: str, request: CredentialCreateRequest
    ) -> CredentialResponse:
        repo = CredentialRepository(self.repo.session)
        cred = Credential(
            owner_id=owner_id,
            name=request.name,
            kind=request.kind,
            target=request.target,
            secret_encrypted=request.secret,
            description=request.description,
        )
        cred = await repo.create(cred)
        return _credential_to_response(cred)

    async def update_credential(
        self, owner_id: str, credential_id: str, request: CredentialUpdateRequest
    ) -> CredentialResponse | None:
        repo = CredentialRepository(self.repo.session)
        cred = await repo.get_by_id(credential_id)
        if not cred or cred.owner_id != owner_id:
            return None
        if request.name is not None:
            cred.name = request.name
        if request.description is not None:
            cred.description = request.description
        if request.secret:
            cred.secret_encrypted = request.secret
        await self.repo.session.flush()
        return _credential_to_response(cred)

    async def delete_credential(self, owner_id: str, credential_id: str) -> bool:
        repo = CredentialRepository(self.repo.session)
        cred = await repo.get_by_id(credential_id)
        if not cred or cred.owner_id != owner_id:
            return False
        await repo.delete(cred)
        return True

    async def resolve_for_task(
        self, owner_id: str, refs: list[str]
    ) -> list[Credential]:
        """任务注入用：按 id 批量取凭据(明文)（校验 owner）"""
        repo = CredentialRepository(self.repo.session)
        return await repo.get_by_ids_for_owner(refs, owner_id)


def _credential_to_response(cred: Credential) -> CredentialResponse:
    plain = cred.secret_encrypted
    return CredentialResponse(
        id=cred.id,
        name=cred.name,
        kind=cred.kind,
        target=cred.target,
        secret_masked=mask_secret(plain),
        has_secret=bool(plain),
        description=cred.description,
        created_at=cred.created_at,
        updated_at=cred.updated_at,
    )
