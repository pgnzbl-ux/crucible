"""
Claude Agent SDK 适配器（worker 侧）。

职责：
1. 构造注入 agent-runner 容器的 LLM 环境变量（凭据零落盘）
2. 拼接最终 prompt（含 system guidance + 任务详情）
3. 容器内完整工具能力与安全策略实现在 runner（server/gateway/policies），经 HTTP/SSE 驱动

与 ClaudeCodeAdapter 区别：
- 旧适配器构造 `claude -p "..." --output-format json` CLI 命令
- 新适配器只构造 Provider 凭据 env（docker env 注入容器）；执行契约经 HTTP AgentSpec 下发
- SDK 调用 + canUseTool + Message 翻译全部下沉到 agent-runner 镜像内
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.settings.repository import SettingsRepository
from app.contexts.settings.service import SettingsService
from app.core.config import get_settings
from app.core.url_security import validate_public_https_url

settings = get_settings()


class ClaudeSdkAdapter:
    """Claude Agent SDK 适配器（worker 侧）。

    职责：统一构造注入 agent-runner 容器的环境变量配置（凭据零落盘）。
    """

    def __init__(self) -> None:
        self.max_turns = settings.claude_sdk_max_turns

    def build_runner_env(self, provider_env: dict[str, str] | None = None) -> dict[str, str]:
        """构造注入 agent-runner 容器的环境变量（凭据零落盘）。

        凭据只来自 provider_env（DB 默认 Provider）；不传则不注入 LLM 凭据。
        """
        env: dict[str, str] = {
            # 强制项：保证容器内 Python 不缓冲输出
            "PYTHONUNBUFFERED": "1",
            # HOME 指向容器 tmpfs（/tmp），不落共享 /workspace，避免后续节点读到上一跳 SDK 缓存
            "HOME": "/tmp",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            # 外层 Docker 已是隔离边界；SCRUB=1 的内层 bwrap 在当前 runner
            # 安全配置下会搞挂 Bash。凭据剥离见 run_one PreToolUse env -u。
            "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "0",
            "CLAUDE_SDK_MAX_TURNS": str(self.max_turns),
        }
        src = provider_env or {}

        base_url = src.get("ANTHROPIC_BASE_URL")
        auth_token = src.get("ANTHROPIC_AUTH_TOKEN")
        api_key = src.get("ANTHROPIC_API_KEY")
        model = src.get("ANTHROPIC_MODEL")
        timeout = src.get("API_TIMEOUT_MS")
        max_context = src.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS")
        effort = src.get("CLAUDE_CODE_EFFORT_LEVEL")
        always_effort = src.get("CLAUDE_CODE_ALWAYS_ENABLE_EFFORT")

        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        if auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        elif api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        if model:
            env["ANTHROPIC_MODEL"] = model
            env["ANTHROPIC_SMALL_FAST_MODEL"] = model
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
        if timeout:
            env["API_TIMEOUT_MS"] = timeout
        if max_context:
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = max_context
        if effort:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
        if always_effort:
            env["CLAUDE_CODE_ALWAYS_ENABLE_EFFORT"] = always_effort

        return env


async def resolve_runner_env(session: AsyncSession) -> dict[str, str]:
    """从后台默认 Provider 构造 docker run --env；无默认 Provider 则不注入凭据。"""
    adapter = ClaudeSdkAdapter()
    svc = SettingsService(SettingsRepository(session))
    provider = await svc.get_default_provider()
    if provider is None:
        return adapter.build_runner_env()
    await validate_public_https_url(provider.base_url)
    return adapter.build_runner_env(svc.build_env_from_provider(provider))


def redact_env_for_log(env: dict[str, str]) -> dict[str, str]:
    """debug 日志前 redact 凭据（security.md §1 凭据零落盘）"""
    redacted = {}
    sensitive_patterns = ("KEY", "TOKEN", "SECRET", "PASSWORD")
    for k, v in env.items():
        if any(p in k.upper() for p in sensitive_patterns):
            if len(v) > 4:
                redacted[k] = f"***{v[-4:]}"
            else:
                redacted[k] = "***"
        else:
            redacted[k] = v
    return redacted
