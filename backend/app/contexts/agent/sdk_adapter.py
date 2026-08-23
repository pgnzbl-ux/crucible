"""
Claude Agent SDK 适配器（worker 侧）。

职责：
1. 构造注入 agent-runner 容器的 LLM 环境变量（凭据零落盘）
2. 拼接最终 prompt（含 system guidance + 任务详情）
3. 容器内权限/黑白名单实现在 runner/run_one.py（独立进程内本地回调，避免 worker 镜像承担 SDK 类型）

与 ClaudeCodeAdapter 区别：
- 旧适配器构造 `claude -p "..." --output-format json` CLI 命令
- 新适配器只构造 env（注入容器）+ 写 .prompt.json（容器内读取）
- SDK 调用 + canUseTool + Message 翻译全部下沉到 agent-runner 镜像内
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.settings.repository import SettingsRepository
from app.contexts.settings.service import SettingsService
from app.core.config import get_settings
from app.core.url_security import validate_public_https_url

settings = get_settings()


# DEPRECATED: 编排走 .node.json,system prompt 由插件 agent frontmatter 提供,此常量无调用方。
# 历史说明：曾与 runner/run_one.py::SYSTEM_PROMPT 保持一致（容器内 SDK system_prompt 选项覆盖）。
SYSTEM_PROMPT = """你是 Crucible AI 辅助代码审计与漏洞挖掘平台的资深安全研究员 Agent。
任务：审计给定项目源码，挖掘并核实漏洞线索；若给出目标漏洞，则执行定向验证。

必须遵守：
1. 白盒优先：先读全源码、走通调用链，再下结论
2. 输出必须包含：结论（存在/不存在/无法确认）、证据链（文件+行号+代码片段）、利用条件、修复建议
3. 诚实：无法确认时明确说明，禁止编造证据
4. 所有分析只读项目源码，不执行任何破坏性命令
"""


class ClaudeSdkAdapter:
    """Claude Agent SDK 适配器（worker 侧）。

    对外提供两件事：
    - build_runner_env() → docker run --env 的 8 个 ANTHROPIC_* 变量 + PYTHONUNBUFFERED
    - build_prompt_payload(ctx) → 写入 /workspace/.prompt.json 的 JSON 内容

    canUseTool 黑白名单实现在容器内（runner/run_one.py），不污染 worker 镜像。
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
            "CLAUDE_SDK_MAX_TURNS": str(self.max_turns),
        }
        src = provider_env or {}

        base_url = src.get("ANTHROPIC_BASE_URL")
        api_key = src.get("ANTHROPIC_AUTH_TOKEN") or src.get("ANTHROPIC_API_KEY")
        model = src.get("ANTHROPIC_MODEL")
        timeout = src.get("API_TIMEOUT_MS")

        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        if api_key:
            # DeepSeek 官方文档使用 ANTHROPIC_AUTH_TOKEN；同时设置 API_KEY 兼容其他端点
            env["ANTHROPIC_AUTH_TOKEN"] = api_key
            env["ANTHROPIC_API_KEY"] = api_key
        if model:
            env["ANTHROPIC_MODEL"] = model
            env["ANTHROPIC_SMALL_FAST_MODEL"] = model
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
        if timeout:
            env["API_TIMEOUT_MS"] = timeout

        return env

    def build_prompt_payload(self, task_ctx: dict[str, Any]) -> str:
        """构造容器内 /workspace/.prompt.json 的内容。

        容器内 run_one.py 读取后自行拼 system_prompt + 任务详情 → 调 query()。
        """
        import json

        return json.dumps(
            {
                "task_id": task_ctx.get("task_id", ""),
                "run_id": task_ctx.get("run_id", ""),
                "project_address": task_ctx.get("project_address", ""),
                "project_ref": task_ctx.get("project_ref") or "",
                "vulnerability_description": task_ctx.get("vulnerability_description", ""),
                "secret_files": task_ctx.get("secret_files", []),
            },
            ensure_ascii=False,
            indent=2,
        )


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
