"""容器执行安全策略 — Bash 逃逸拦截、凭据剥离、压缩产物读守卫、Stop 催交。

定位：这些是「一次性容器沙箱」的执行环境安全策略（generic runner 职责），
不是业务逻辑。策略决策经由 EventSink 产出审计事件（tool.call.denied /
tool.call.scrubbed / phase.updated），由 gateway 统一编入事件流与 transcript。

安全模型：容器本身是一次性（Ephemeral）隔离沙箱，任务结束即销毁：
1. 放开 AI 的 Linux/Bash 工具能力（管道、rm、curl、npm 等）；
2. 仅拦截 docker/docker-compose/podman 可执行命令，防容器逃逸与宿主 Daemon 探测
   （正则只做第一道闸，容器隔离才是真实边界）；
3. 凭据安全：Bash 命令强制包装 env -u，子进程看不到 Provider 主凭据。
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# docker / docker-compose / podman 作可执行命令时拒绝；不得误伤 host.docker.internal 字符串
_DOCKER_CMD_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:\S*/)?(?:docker(?:-compose)?|podman)(?:\s|$)"
)

# 凭据剥离包装：Bash 子进程看不到 Provider 主凭据；CLI 父进程环境仍保留供 API
_CRED_UNSET_PREFIX = (
    "env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN "
    "bash --noprofile --norc -c "
)

# 压缩产物判定默认阈值（backend 可经平台策略覆盖时再下发）
DEFAULT_MINIFIED_MIN_BYTES = 300_000
DEFAULT_MINIFIED_MAX_LINES = 500
_MINIFIED_LINE_SCAN_BYTES = 4 * 1024 * 1024

EventSink = Callable[[dict[str, Any]], None]
HookFn = Callable[[Any, str | None, Any], Awaitable[dict | None]]


def classify_bash(cmd: str) -> tuple[str, str | None]:
    """返回 (decision, reason)：除容器逃逸命令外全面放行。"""
    if _DOCKER_CMD_RE.search(cmd or ""):
        return ("deny", "blocked by policy: docker escape")
    return ("allow", None)


def bash_command_without_provider_creds(cmd: str) -> str:
    """Bash 命令包装 env -u 剥凭据；已包装的命令幂等跳过。"""
    text = (cmd or "").strip()
    if not text:
        return cmd
    if text.startswith(_CRED_UNSET_PREFIX) or f" {_CRED_UNSET_PREFIX}" in f" {text}":
        return cmd
    return f"{_CRED_UNSET_PREFIX}{shlex.quote(text)}"


def is_minified_file(
    path: Path,
    *,
    min_bytes: int = DEFAULT_MINIFIED_MIN_BYTES,
    max_lines: int = DEFAULT_MINIFIED_MAX_LINES,
) -> tuple[bool, int, int]:
    """返回 (是否压缩产物, 大小字节, 行数)；stat/读失败按未命中处理。

    打包前端资源常被压成几行、每行数百 KB：Read 按行取必然 exceeds maximum
    allowed size，Grep 会回吐整行截断噪声。
    """
    try:
        if not path.is_file():
            return False, 0, 0
        size = path.stat().st_size
    except OSError:
        return False, 0, 0
    if size < min_bytes:
        return False, size, 0
    lines = 0
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                lines += chunk.count(b"\n")
                if fh.tell() >= _MINIFIED_LINE_SCAN_BYTES:
                    break
    except OSError:
        return False, size, 0
    lines += 1
    return lines <= max_lines, size, lines


def _hook_tool_fields(hook_input: Any) -> tuple[str, dict[str, Any]]:
    """从 PreToolUseHookInput（TypedDict 或鸭子类型）取 (tool_name, tool_input)。"""
    getter = hook_input.get if isinstance(hook_input, dict) else lambda k, d=None: getattr(hook_input, k, d)
    tool_name = getter("tool_name", "") or ""
    tool_input = getter("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {"command": str(tool_input)}
    return str(tool_name), tool_input


def make_bash_policy_hook(sink: EventSink) -> HookFn:
    """PreToolUse hook（matcher=Bash）：逃逸拦截 + 凭据剥离。

    SDK 0.2.x hooks 直接接受 async 回调，返回 SyncHookJSONOutput；
    permissionDecision 在所有 permission_mode（含 bypassPermissions）下生效。
    updatedInput 是整对象替换，必须带回全部原字段。
    """

    async def bash_policy_hook(hook_input: Any, _tool_use_id: str | None, _ctx: Any) -> dict | None:
        tool_name, tool_input = _hook_tool_fields(hook_input)
        if tool_name != "Bash":
            return {}
        cmd = str(tool_input.get("command", "") or "")
        decision, reason = classify_bash(cmd)
        if decision == "deny":
            sink(
                {
                    "type": "tool.call.denied",
                    "tool": tool_name,
                    "reason": reason,
                    "input": cmd[:200],
                }
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason or "denied by policy",
                }
            }
        scrubbed = bash_command_without_provider_creds(cmd)
        if scrubbed != cmd:
            sink(
                {
                    "type": "tool.call.scrubbed",
                    "tool": tool_name,
                    "input": cmd[:200],
                }
            )
        updated = {**tool_input, "command": scrubbed}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "updatedInput": updated,
            }
        }

    return bash_policy_hook


def make_read_guard_hook(sink: EventSink, *, workspace_root: str = "/workspace") -> HookFn:
    """PreToolUse hook（matcher=Read/Grep/Edit）：压缩产物 deny 并指路 read_slice。

    deny reason 直接给出有界替代，模型可一次转向，不在报错上空转。
    """

    async def read_guard_hook(hook_input: Any, _tool_use_id: str | None, _ctx: Any) -> dict | None:
        tool_name, tool_input = _hook_tool_fields(hook_input)
        if tool_name not in ("Read", "Grep", "Edit"):
            return {}
        raw_path = str(
            tool_input.get("file_path")
            or tool_input.get("path")
            or tool_input.get("filePath")
            or ""
        )
        if not raw_path:
            return {}
        target = Path(raw_path)
        if not target.is_absolute():
            target = Path(workspace_root) / target
        target = target.resolve()
        minified, size, lines = is_minified_file(target)
        if not minified:
            return {}
        mb = size / 1_000_000
        reason = (
            f"{target.name} 是压缩/打包产物（{mb:.1f}MB 仅 {lines} 行），"
            f"{tool_name} 按行操作必然超出工具大小上限。"
            "改用 mcp__crucible__read_slice(file_path=..., pattern=<关键词或正则>, "
            f"context=300) 按命中点取有界片段；或 Bash: grep -oE '.{{120}}<关键词>.{{0,240}}' <文件>。"
            f"不要重试 {tool_name}。"
        )
        sink(
            {
                "type": "tool.call.denied",
                "tool": tool_name,
                "reason": f"minified artifact: {target.name} ({mb:.1f}MB/{lines} 行)",
                "input": raw_path[:200],
            }
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return read_guard_hook


def make_stop_hook(sink: EventSink, *, output_path: str) -> HookFn:
    """Stop hook：会话欲结束时若还没写提交产物，block 一次催交。

    stop_hook_active=true 表示已催过一轮；再 block 只会在上下文将满时空转，
    交给 submit-missing 失败路径收尾。
    """

    async def stop_hook(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        del tool_use_id, context
        out = Path(output_path)
        if out.is_file():
            return {}
        hook_input = input_data if isinstance(input_data, dict) else {}
        if hook_input.get("stop_hook_active"):
            return {}
        reason = (
            "本任务尚未调用 submit_result。"
            "立即调用 mcp__crucible__submit_result 提交完整结构化结果。"
            "不要再做探索性工具调用；提交当前能确定的结论。"
        )
        sink(
            {
                "type": "phase.updated",
                "phase": "submit_nudge",
                "message": "会话欲结束但未调用 submit_result，已催交一次",
            }
        )
        return {"decision": "block", "reason": reason}

    return stop_hook


def build_policy_hooks(
    sink: EventSink,
    *,
    workspace_root: str = "/workspace",
    output_path: str,
    submit_enforced: bool,
) -> dict[str, list[Any]]:
    """组装 ClaudeAgentOptions.hooks 结构（必须用 HookMatcher 实例：
    裸 dict 会被 SDK _convert_hooks_to_internal_format 静默丢弃）。
    """
    from claude_agent_sdk import HookMatcher

    hooks: dict[str, list[HookMatcher]] = {
        "PreToolUse": [
            HookMatcher(matcher="Bash", hooks=[make_bash_policy_hook(sink)]),
            HookMatcher(matcher="Read", hooks=[make_read_guard_hook(sink, workspace_root=workspace_root)]),
            HookMatcher(matcher="Grep", hooks=[make_read_guard_hook(sink, workspace_root=workspace_root)]),
            HookMatcher(matcher="Edit", hooks=[make_read_guard_hook(sink, workspace_root=workspace_root)]),
        ],
    }
    if submit_enforced:
        hooks["Stop"] = [HookMatcher(hooks=[make_stop_hook(sink, output_path=output_path)])]
    return hooks
