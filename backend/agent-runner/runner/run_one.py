"""
agent-runner 容器内 entrypoint。

职责：读取 /workspace/.node.json（节点模式）或 .prompt.json（兼容）→
       按 NODE_KEY 加载蒸馏 skill 作 system_prompt → 调用 query() →
       逐条翻译 SDK Message 为统一事件结构 → 写到 stdout（JSONL 一行一条）。

环境变量由 worker 在 docker run 时通过 --env 注入：
  ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / ANTHROPIC_MODEL
  ANTHROPIC_SMALL_FAST_MODEL / ANTHROPIC_DEFAULT_HAIKU_MODEL
  API_TIMEOUT_MS / CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC
  CLAUDE_CODE_SUBPROCESS_ENV_SCRUB / PYTHONUNBUFFERED=1

退出码：
  0  = 正常完成（含 conclusion=unconfirmed 等业务软失败）
  1  = 业务失败（LLM error / 无产出）
  2  = 基础设施错误（OOM / 网络断开 / 凭据缺失）
  137 = SIGKILL（被 worker revoke）

设计要点：
- SDK 在容器内解析 Message → 翻译为 dict → json.dumps 到 stdout；
  worker 侧只 json.loads 每行，不感知 SDK 类型。
- 完整 Claude Code 工具集配合 bypassPermissions 实现无人值守自动化；
  PreToolUse hook 只拦截平台明确禁止的 Bash 命令并输出审计事件。
  Stop hook 在节点尚未 submit_result 时催交一次，避免会话正常结束却无产物。
- stderr 走 SDK 自身 logging（便于 docker logs 调试），不污染 JSONL 流。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import traceback
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        HookMatcher,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
        query,
    )
except ImportError as e:  # 镜像构建失败时给出明确报错
    print(
        json.dumps(
            {
                "type": "agent.failed",
                "error": f"claude_agent_sdk 导入失败: {e}",
                "title": "容器内缺少 Claude Agent SDK",
                "hint": "agent-runner 镜像不完整，请重新构建镜像。",
                "exception": type(e).__name__,
                "sequence": 0,
                "timestamp": time.time(),
            }
        ),
        file=sys.stdout,
        flush=True,
    )
    sys.exit(2)

try:
    from claude_agent_sdk import ThinkingBlock  # type: ignore[attr-defined]
except ImportError:
    ThinkingBlock = None  # type: ignore[misc, assignment]


# ── Bash 黑名单（核心安全规则，PreToolUse hook 消费） ──
#
# 策略（v0.3）：完整工具能力 + PreToolUse 黑名单。
# - Bash：黑名单拦截破坏性命令（rm/mv/chmod/dd/mkfs/|bash/>/etc//proc//sys），
#   其余放开（插件工作流需要 git / curl / python / node / 常规 Linux 命令）
# - allowed_tools 仅是自动批准提示，不是工具白名单；bypassPermissions 下未列出的
#   工具仍可执行。真正的硬拒绝由本 hook 和容器边界负责。
# - can_use_tool 不再用：bypassPermissions 会 shadow 它（SDK CanUseToolShadowedWarning）；
#   PreToolUse hook 在所有 permission_mode 下都执行（CLI 原生消费 permissionDecision）

BLACKLIST_RES = [
    (re.compile(r"\|\s*bash\b"), "| bash"),
    (re.compile(r"\|\s*sh\b"), "| sh"),
    (re.compile(r">\s*/etc/"), "> /etc/"),
    (re.compile(r">\s*~/\.bashrc"), "> ~/.bashrc"),
    (re.compile(r"^rm\s+-rf?\s"), "rm -rf"),
    (re.compile(r"^rm\s"), "rm"),
    (re.compile(r"^mv\s"), "mv"),
    (re.compile(r"^cp\s"), "cp"),
    (re.compile(r"^chmod\b"), "chmod"),
    (re.compile(r"^chown\b"), "chown"),
    (re.compile(r"^dd\s"), "dd"),
    (re.compile(r"^mkfs\b"), "mkfs"),
    (re.compile(r"/proc/"), "/proc/"),
    (re.compile(r"/sys/"), "/sys/"),
]

# docker / docker-compose 作可执行命令时拒绝；不得误伤 host.docker.internal
_DOCKER_CMD_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:sudo\s+)?(?:\S*/)?docker(?:-compose)?(?:\s|$)"
)

# env_ready 额外拒绝：装依赖 / docker（配方由平台 compose 启动，Agent 只写文件）
ENV_READY_DENY_RES = [
    (re.compile(r"\bnpm\b"), "npm"),
    (re.compile(r"\bnpx\b"), "npx"),
    (re.compile(r"\byarn\b"), "yarn"),
    (re.compile(r"\bpnpm\b"), "pnpm"),
    (re.compile(r"\bpip3?\b"), "pip"),
    (re.compile(r"\bapt(-get)?\b"), "apt"),
    (re.compile(r"\bapk\b"), "apk"),
    (re.compile(r"\b(yum|dnf)\b"), "yum"),
    (_DOCKER_CMD_RE, "docker"),
]

# audit 额外拒绝：HTTP 客户端（白盒节点禁止打活靶；不拦 python urllib）
AUDIT_DENY_RES = [
    (re.compile(r"\bcurl\b"), "curl"),
    (re.compile(r"\bwget\b"), "wget"),
    (re.compile(r"\bhttpie\b"), "httpie"),
]

# reproduce 额外拒绝：靶场已由平台 compose 启动，禁止 Agent 自己 docker
REPRODUCE_DENY_RES = [
    (_DOCKER_CMD_RE, "docker"),
]

# ── 压缩产物判定（与 backend app/contexts/project/source_minified.py 保持一致）──
# 打包前端资源常被压成几行、每行数百 KB：Read 按行取必然 exceeds maximum
# allowed size，Grep 会回吐整行截断噪声。命中即 deny 并引导改用 read_slice。
_WORKSPACE_ROOT = "/workspace"
_MINIFIED_MIN_BYTES = 300_000
_MINIFIED_MAX_LINES = 500
_MINIFIED_LINE_SCAN_BYTES = 4 * 1024 * 1024


def _is_minified_file(path: Path) -> tuple[bool, int, int]:
    """返回 (是否压缩产物, 大小字节, 行数)；stat/读失败按未命中处理。"""
    try:
        if not path.is_file():
            return False, 0, 0
        size = path.stat().st_size
    except OSError:
        return False, 0, 0
    if size < _MINIFIED_MIN_BYTES:
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
    return lines <= _MINIFIED_MAX_LINES, size, lines

def _allowed_tools_for(node_key: str | None) -> list[str]:
    """返回节点常用工具的自动批准提示；不作为能力或安全边界。"""
    if node_key == "canary":
        return ["Read", "Bash"]
    if node_key == "profile":
        return ["Read", "Grep", "Glob"]
    if node_key == "triage":
        return ["Read", "Grep", "Glob"]
    if node_key == "audit":
        return ["Read", "Grep", "Glob", "Bash", "WebSearch"]
    return ["Read", "Grep", "Glob", "Bash", "Write", "Edit", "WebFetch", "WebSearch"]


def _classify_bash(cmd: str, node_key: str | None = None) -> tuple[str, str | None]:
    """返回 (decision, reason)：先全局黑名单，再按节点额外拒绝。"""
    for pat, name in BLACKLIST_RES:
        if pat.search(cmd):
            return ("deny", f"blocked by policy: {name}")
    if node_key == "env_ready":
        for pat, name in ENV_READY_DENY_RES:
            if pat.search(cmd):
                return ("deny", f"blocked by policy: {name}")
    if node_key == "audit":
        for pat, name in AUDIT_DENY_RES:
            if pat.search(cmd):
                return ("deny", f"blocked by policy: {name}")
    if node_key == "reproduce":
        for pat, name in REPRODUCE_DENY_RES:
            if pat.search(cmd):
                return ("deny", f"blocked by policy: {name}")
    return ("allow", None)


async def _pre_tool_use_hook(
    hook_input: Any, _tool_use_id: str | None, _ctx: Any
) -> dict | None:
    """PreToolUse hook：Bash 黑名单拦截；放行时剥离 Provider 凭据后再执行。

    SDK 0.2.x 的 hooks 字段直接接受 async 回调（非 shell command），返回
    SyncHookJSONOutput。permissionDecision 由 _bundled/claude CLI 原生消费，
    在所有 permission_mode（含 bypassPermissions）下都生效。

    matcher 限定 Bash，故本回调只处理 Bash；Write/Edit/WebFetch 等不触发。
    """
    # hook_input 是 PreToolUseHookInput（TypedDict），按字段取
    tool_name = (
        hook_input.get("tool_name")
        if isinstance(hook_input, dict)
        else getattr(hook_input, "tool_name", "")
    ) or ""
    tool_input = (
        hook_input.get("tool_input")
        if isinstance(hook_input, dict)
        else getattr(hook_input, "tool_input", {})
    )
    if tool_name != "Bash":
        return {}  # 非 Bash 不处理（matcher 已限定，兜底）

    if not isinstance(tool_input, dict):
        tool_input = {"command": str(tool_input)}
    cmd = str(tool_input.get("command", "") or "")
    decision, reason = _classify_bash(cmd, os.environ.get("NODE_KEY"))
    if decision == "deny":
        # 审计事件（worker 侧落 AgentEvent，前端可见）
        print(
            json.dumps(
                {
                    "type": "tool.call.denied",
                    "tool": tool_name,
                    "reason": reason,
                    "input": cmd[:200],
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason or "denied by policy",
            }
        }
    # 放行：用 env -u 剥凭据，避免 SCRUB=1/bwrap 在锁定 Docker 内搞挂 Bash。
    # updatedInput 是整对象替换，必须带回全部原字段。
    scrubbed = _bash_command_without_provider_creds(cmd)
    if scrubbed != cmd:
        print(
            json.dumps(
                {
                    "type": "tool.call.scrubbed",
                    "tool": tool_name,
                    "input": cmd[:200],
                    "timestamp": time.time(),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    updated = {**tool_input, "command": scrubbed}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }


async def _read_guard_hook(
    hook_input: Any, _tool_use_id: str | None, _ctx: Any
) -> dict | None:
    """PreToolUse hook：Read/Grep 目标命中压缩产物（大而少行）时 deny。

    压缩打包文件单行数百 KB：Read 按行取必然 exceeds maximum allowed size，
    Grep 会回吐整行截断噪声烧上下文。deny reason 直接给出有界替代
    （read_slice / grep -oE），模型可一次转向，不在报错上空转。
    matcher 分两条注册（Read / Grep），本回调按 tool_name 分流。
    """
    tool_name = (
        hook_input.get("tool_name")
        if isinstance(hook_input, dict)
        else getattr(hook_input, "tool_name", "")
    ) or ""
    if tool_name not in ("Read", "Grep"):
        return {}
    tool_input = (
        hook_input.get("tool_input")
        if isinstance(hook_input, dict)
        else getattr(hook_input, "tool_input", {})
    )
    if not isinstance(tool_input, dict):
        return {}
    raw_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    if not raw_path:
        return {}
    target = Path(raw_path)
    if not target.is_absolute():
        target = Path(_WORKSPACE_ROOT) / target
    target = target.resolve()
    minified, size, lines = _is_minified_file(target)
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
    print(
        json.dumps(
            {
                "type": "tool.call.denied",
                "tool": tool_name,
                "reason": f"minified artifact: {target.name} ({mb:.1f}MB/{lines} 行)",
                "input": raw_path[:200],
                "timestamp": time.time(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


async def _stop_hook(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
    """会话欲结束时：若本节点还没写 submit_result 产物，block 一次催交。

    stop_hook_active=true 表示已经催过一轮。再 block 会在上下文将满时空转烧钱，
    交给现有 no_submit 失败路径。
    """
    del tool_use_id, context
    if not os.environ.get("NODE_KEY"):
        return {}
    out_path = Path(
        os.environ.get("NODE_OUTPUT_PATH", "/workspace/.node_output.json")
    )
    if out_path.is_file():
        return {}
    hook_input = input_data if isinstance(input_data, dict) else {}
    if hook_input.get("stop_hook_active"):
        return {}
    reason = (
        "本节点尚未调用 submit_result。"
        "立即调用 mcp__crucible__submit_result 提交完整结构化结果。"
        "不要再做探索性工具调用（不要再发 HTTP、不要再读大段源码、不要写报告）。"
        "按本节点 skill 的完成条款提交；打不出危害也要提交当前能确定的判定。"
    )
    print(
        json.dumps(
            {
                "type": "phase.updated",
                "phase": "submit_nudge",
                "message": "会话欲结束但未调用 submit_result，已催交一次",
                "timestamp": time.time(),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return {"decision": "block", "reason": reason}


_CRED_UNSET_PREFIX = (
    "env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN "
    "bash --noprofile --norc -c "
)


def _bash_command_without_provider_creds(cmd: str) -> str:
    """Bash 子进程看不到 Provider 主凭据；CLI 父进程环境仍保留供 API。"""
    import shlex

    text = (cmd or "").strip()
    if not text:
        return cmd
    if text.startswith(_CRED_UNSET_PREFIX) or f" {_CRED_UNSET_PREFIX}" in f" {text}":
        return cmd
    return f"{_CRED_UNSET_PREFIX}{shlex.quote(text)}"


# ── SDK Message → 统一事件结构翻译 ──


def _safe_get(obj: Any, *path: str, default: Any = None) -> Any:
    """嵌套字段安全访问（兼容 SDK 不同版本的属性差异）"""
    cur = obj
    for key in path:
        try:
            cur = getattr(cur, key, None) or (
                cur.get(key) if isinstance(cur, dict) else None
            )
        except (AttributeError, KeyError):
            return default
        if cur is None:
            return default
    return cur


def _truncate(text: Any, limit: int) -> str:
    if not isinstance(text, str):
        text = str(text)
    return text[:limit]


def extract_thinking_text(block: Any) -> str | None:
    """从 SDK content block 抽出思考文本（兼容 ThinkingBlock / 鸭子类型 / dict）。"""
    if block is None:
        return None
    if isinstance(ThinkingBlock, type):
        try:
            if isinstance(block, ThinkingBlock):
                text = getattr(block, "thinking", None) or getattr(block, "text", None)
                return str(text) if text else None
        except TypeError:
            pass
    thinking = getattr(block, "thinking", None)
    if isinstance(thinking, str) and thinking.strip():
        return thinking
    name = type(block).__name__.lower()
    if "thinking" in name:
        text = getattr(block, "text", None) or getattr(block, "thinking", None)
        return str(text) if text else None
    if isinstance(block, dict):
        btype = str(block.get("type") or "")
        if btype in ("thinking", "thought"):
            raw = block.get("thinking") or block.get("text") or ""
            return str(raw) if raw else None
    return None


def humanize_container_error(raw: str) -> tuple[str, str]:
    """容器内失败 → (标题, 下一步)。与 worker 侧 errors.py 对齐。"""
    text = (raw or "").strip() or "未知错误"
    low = text.lower()
    if "bubblewrap is required" in low:
        return (
            "Agent 运行环境缺少进程隔离依赖",
            "请重建 Agent 运行镜像（需包含 bubblewrap）后重试。",
        )
    if "sandbox dependencies not available" in low:
        return (
            "Agent 运行环境缺少沙箱运行依赖",
            "请重建 Agent 运行镜像（需包含 bubblewrap 与 socat）后重试。",
        )
    if "no permissions to create new namespace" in low:
        return (
            "Agent 嵌套沙箱被 Docker 拦截",
            "当前安全策略阻止了沙箱命名空间（seccomp）。请更新并重启 API 与 Worker 后重试。",
        )
    if any(
        marker in low
        for marker in (
            "context size has been exceeded",
            "context window exceeded",
            "maximum context length",
            "prompt is too long",
        )
    ):
        return (
            "LLM 上下文窗口不足",
            "调低 Provider 最大上下文，或减少单轮工具输出后重试。",
        )
    if "余额不足" in text or '"code":"1004"' in text or '"code": "1004"' in text:
        return (
            "LLM 账户余额不足",
            "到服务商控制台充值，或更换可用的 API Key，再在「设置 → LLM Provider」更新后重试。",
        )
    if "http 401" in low:
        return (
            "LLM 接口鉴权失败（401）",
            "检查 API Key、Base URL 与账户状态；部分网关在余额不足时也会返回 401。",
        )
    if "error result: success" in low:
        return (
            "LLM 会话异常结束",
            "多为上游接口报错（余额不足、模型不可用等）。请查看本节点更早的失败事件，并核对 Provider 配置。",
        )
    rules = [
        (
            "未调用 submit_result",
            "Agent 没有提交节点结果",
            "本轮分析未完成结构化回传。可从本节点重试；若反复出现，请检查 Provider 是否通过 Agent 测试。",
        ),
        (
            "claude_agent_sdk 导入失败",
            "Agent 运行环境不完整",
            "请重建 Agent 运行镜像后重试。",
        ),
        ("NameError", "Agent 运行入口异常", "请更新代码并重建 Agent 运行镜像后重试。"),
        ("Authentication", "LLM 鉴权失败", "检查 API Key 与 Base URL。"),
        (
            ".node.json 解析失败",
            "节点输入文件损坏",
            "请从本节点重试；持续失败时联系管理员检查任务调度。",
        ),
        (
            "既无 .node.json 也无 .prompt.json",
            "容器未收到任务输入",
            "请从本节点重试；持续失败时检查任务工作目录挂载是否正常。",
        ),
    ]
    for needle, title, hint in rules:
        if needle.lower() in low:
            return title, hint
    return text[:240], "查看本节点事件流中的错误与工具输出，定位失败步骤后重试。"


def _is_llm_api_failure(text: str) -> bool:
    low = (text or "").lower()
    if not low.strip():
        return False
    needles = (
        "http 401",
        "http 403",
        "http 429",
        "余额不足",
        '"code":"1004"',
        "api error: 4",
        "api error: 5",
        "error result: success",
        "model_not_found",
        "rate limit",
        "context size has been exceeded",
        "context window exceeded",
        "maximum context length",
        "prompt is too long",
    )
    return any(n in low for n in needles)


def _failed_event(raw: str, **extra: Any) -> dict[str, Any]:
    title, hint = humanize_container_error(raw)
    return {
        "type": "agent.failed",
        "error": _truncate(raw, 500),
        "title": title,
        "hint": hint,
        **extra,
    }


def _classify_conclusion(text: str) -> str:
    """文本匹配：exists / not_exists / unconfirmed"""
    if not text:
        return "unconfirmed"
    lowered = text.lower()
    if any(
        k in lowered
        for k in (
            "漏洞存在",
            "确认存在",
            "reproduced",
            "confirmed",
            "vulnerable",
            "is exploitable",
            "结论：存在",
            "存在漏洞",
        )
    ):
        return "exists"
    if any(
        k in lowered
        for k in (
            "不存在",
            "无法确认",
            "not vulnerable",
            "not exploitable",
            "unconfirmed",
            "结论：不存在",
            "误报",
        )
    ):
        return "not_exists"
    return "unconfirmed"


# SDK SystemMessage 里 thinking_tokens 是逐 token 用量心跳，不是阶段变更；
# 但 mcp_server_error 等 subtype 携带工具注入失败信息，必须透传供排障
# （2026-08-19 audit 教训：MCP 工具被网关丢弃时唯一的线索在这类消息里）
_KEEP_SYSTEM_SUBTYPES = frozenset({"init", "mcp_server_error", "stream_error"})


def _system_phase_event(
    message: Any,
    *,
    seq: int,
    timestamp: float,
    session_id: str | None,
) -> dict[str, Any] | None:
    subtype = getattr(message, "subtype", None) or "init"
    if subtype not in _KEEP_SYSTEM_SUBTYPES:
        return None
    if subtype == "init":
        phase = "start"
        message_text = subtype
    else:
        phase = "warning"
        message_text = f"{subtype}: {json.dumps(getattr(message, 'data', {}), ensure_ascii=False, default=str)[:400]}"
    return {
        "type": "phase.updated",
        "phase": phase,
        "message": message_text,
        "session_id": session_id,
        "sequence": seq,
        "timestamp": timestamp,
    }


async def _stream_messages(
    options: ClaudeAgentOptions,
    prompt: str,
    node_key: str | None = None,
) -> AsyncIterator[dict]:
    """包裹 SDK async generator，把每条 Message 翻译为 dict。

    每个事件额外携带 `parent_tool_use_id`（None = 主 Agent 线程；Task 子代理
    的内层消息带其 Task 调用 id），前端据此做主/子代理线程分组。
    """
    seq = 0
    session_id_seen: str | None = None
    # tool_use_id -> {"tool": 名称, "command": Bash 命令}：
    # 结果块只有 id 没有名字，用 started 侧登记回填，前端可合并命令+结果
    tool_meta_by_id: dict[str, dict[str, str]] = {}

    try:
        async for message in query(prompt=prompt, options=options):
            seq += 1
            ts = time.time()
            sid = getattr(message, "session_id", None) or session_id_seen
            parent = getattr(message, "parent_tool_use_id", None) or None
            message_type = type(message).__name__

            def _base() -> dict[str, Any]:
                return {"session_id": sid, "sequence": seq, "timestamp": ts}

            # Task 子代理生命周期：SDK 以 SystemMessage 形态推送，
            # 此前被 keep-set 静默丢弃，导致前端无法感知子代理列表/状态
            if isinstance(message, SystemMessage):
                if sid and sid != session_id_seen:
                    session_id_seen = sid
                subtype = getattr(message, "subtype", None) or ""
                if subtype in {
                    "task_started", "task_progress", "task_notification", "task_updated",
                }:
                    data = getattr(message, "data", {}) or {}
                    yield {
                        "type": "agent.subagent.updated",
                        "subtype": subtype,
                        "tool_use_id": (
                            data.get("task_id")
                            or data.get("tool_use_id")
                            or data.get("id")
                            or ""
                        ),
                        "label": (
                            data.get("description")
                            or data.get("subject")
                            or data.get("title")
                            or ""
                        ),
                        "status": data.get("status") or "",
                        "detail": _truncate(
                            json.dumps(data, ensure_ascii=False, default=str), 400
                        ),
                        "parent_tool_use_id": parent,
                        **_base(),
                    }
                    continue
                event = _system_phase_event(
                    message, seq=seq, timestamp=ts, session_id=sid
                )
                if event:
                    yield event
                continue

            # AssistantMessage（含 TextBlock / ToolUseBlock / 错误）
            if isinstance(message, AssistantMessage):
                # 错误分支
                err = getattr(message, "error", None)
                if err:
                    err_msg = getattr(err, "message", str(err))
                    ev = _failed_event(
                        err_msg,
                        model=getattr(message, "model", None),
                        session_id=sid,
                        sequence=seq,
                        timestamp=ts,
                    )
                    ev["parent_tool_use_id"] = parent
                    yield ev
                    continue

                content = getattr(message, "content", None) or []
                if not isinstance(content, list):
                    content = [content]

                for block in content:
                    thinking_text = extract_thinking_text(block)
                    if thinking_text:
                        yield {
                            "type": "agent.thinking",
                            "text": thinking_text,
                            "model": getattr(message, "model", None),
                            "parent_tool_use_id": parent,
                            **_base(),
                        }
                        continue
                    if isinstance(block, TextBlock):
                        yield {
                            "type": "agent.message",
                            "text": getattr(block, "text", "") or "",
                            "model": getattr(message, "model", None),
                            "parent_tool_use_id": parent,
                            **_base(),
                        }
                    elif isinstance(block, ToolUseBlock):
                        tool_name = getattr(block, "name", "unknown")
                        tu_id = getattr(block, "id", None)
                        meta_entry: dict[str, str] = {"tool": tool_name}
                        if tool_name == "Bash":
                            cmd = (getattr(block, "input", {}) or {}).get("command")
                            if cmd:
                                meta_entry["command"] = str(cmd)
                        if tu_id:
                            tool_meta_by_id[tu_id] = meta_entry
                        yield {
                            "type": "tool.call.started",
                            "tool": tool_name,
                            "input": getattr(block, "input", {}) or {},
                            "tool_use_id": tu_id,
                            "parent_tool_use_id": parent,
                            **_base(),
                        }
                    elif not isinstance(block, (TextBlock, ToolUseBlock)):
                        # 未知 block：仍尝试当思考/文本露出，避免静默丢流
                        fallback = getattr(block, "text", None)
                        if fallback:
                            yield {
                                "type": "agent.message",
                                "text": str(fallback),
                                "model": getattr(message, "model", None),
                                "session_id": sid,
                                "sequence": seq,
                                "timestamp": ts,
                            }
                continue

            # UserMessage（含 ToolResultBlock）
            if isinstance(message, UserMessage):
                content = getattr(message, "content", None) or []
                if not isinstance(content, list):
                    content = [content]
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        # content 形态：str | list[dict]（SDK 0.2.x 原样透传 CLI 的
                        # 块列表，Agent 工具结果恒为 [{"type":"text","text":...}]）
                        # | list[对象]（旧形态兼容）。只提取文本，跳过 image 等块。
                        raw_content = getattr(block, "content", "") or ""
                        if isinstance(raw_content, list):
                            parts: list[str] = []
                            for b in raw_content:
                                t = (
                                    b.get("text")
                                    if isinstance(b, dict)
                                    else getattr(b, "text", None)
                                )
                                if isinstance(t, str) and t:
                                    parts.append(t)
                            raw_content = " ".join(parts)
                        result_id = getattr(block, "tool_use_id", None)
                        meta = tool_meta_by_id.get(result_id) or {}
                        event: dict[str, Any] = {
                            "type": "tool.call.completed",
                            "tool_use_id": result_id,
                            "output": _truncate(raw_content, 2000),
                            "is_error": bool(getattr(block, "is_error", False)),
                            # started 侧登记回填：前端合并命令+结果、按名渲染图标
                            "parent_tool_use_id": parent,
                            **_base(),
                        }
                        if meta.get("tool"):
                            event["tool"] = meta["tool"]
                        if meta.get("command"):
                            event["command"] = meta["command"]
                        yield event
                continue

            # ResultMessage（终态）
            if isinstance(message, ResultMessage):
                result_text = getattr(message, "result", "") or ""
                is_error = bool(getattr(message, "is_error", False))
                if is_error:
                    yield _failed_event(
                        result_text or "SDK ResultMessage.is_error=true",
                        session_id=sid,
                        sequence=seq,
                        timestamp=ts,
                    )
                    # 一个 SDK 会话只能有一个终态。失败结果不能继续伪装成 completed。
                    continue
                # usage = 主环；model_usage = 整树（官方 prefer）。透传有则记，禁止自算。
                yield {
                    "type": "agent.completed",
                    **(
                        {"conclusion": _classify_conclusion(result_text)}
                        if not node_key
                        else {}
                    ),
                    "reasoning": result_text,
                    "session_id": sid,
                    "duration_ms": getattr(message, "duration_ms", None),
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "num_turns": getattr(message, "num_turns", None),
                    "usage": _usage_jsonable(getattr(message, "usage", None)),
                    "model_usage": _usage_jsonable(getattr(message, "model_usage", None)),
                    "is_error": False,
                    "sequence": seq,
                    "timestamp": ts,
                }
                continue

            # 未知 Message 类型：原样序列化（兜底，page-ui 可能忽略）
            yield {
                "type": "raw.message",
                "message_type": message_type,
                "session_id": sid,
                "parent_tool_use_id": parent,
                "raw": _truncate(str(message), 500),
                "sequence": seq,
                "timestamp": ts,
            }

    except Exception as e:
        seq += 1
        yield _failed_event(
            str(e),
            exception=type(e).__name__,
            traceback=_truncate(traceback.format_exc(), 1000),
            sequence=seq,
            timestamp=time.time(),
        )
        raise


# ── Prompt 构造 ──
#
# 节点模式：system_prompt = 蒸馏 SKILL.md（append 到 claude_code preset）；
# user message 只含本轮 input_json。不加载桌面 plugins/。


def _build_prompt(task: dict[str, Any]) -> str:
    """构造发给插件 agent 的 user message（只含任务信息，不含 system prompt）。"""
    parts = [
        f"项目地址: {task.get('project_address', '')}",
        f"项目引用: {task.get('project_ref') or 'default branch'}（已 clone 到工作区仓库目录）",
        "",
        f"待验证漏洞描述:\n{task.get('vulnerability_description', '')}",
    ]

    # 平台注入的凭据（P1-6 Credential Proxy）：告知 agent 有哪些 env / 文件可用
    secret_files = task.get("secret_files") or []
    if secret_files:
        envcreds = [s for s in secret_files if s.get("kind") == "env_var"]
        filecreds = [s for s in secret_files if s.get("kind") == "file"]
        parts.append("")
        parts.append("平台已为本次任务注入以下凭据（按需使用，勿外泄）:")
        if envcreds:
            parts.append(
                "- 环境变量: " + ", ".join(s.get("target", "?") for s in envcreds)
            )
        if filecreds:
            parts.append("- 密钥文件（容器内路径，权限 600）:")
            for s in filecreds:
                desc = f" ({s['description']})" if s.get("description") else ""
                parts.append(f"    {s.get('path', '?')}{desc}")

    parts.append("")
    parts.append(
        "请按你的工作流（阶段 A→B→C→D）验证上述漏洞是否真实存在，并用 phase.updated "
        "事件记录每个阶段进度，最终产出中文报告。"
    )
    return "\n".join(parts)


def _container_source_dir(
    input_json: dict[str, Any] | None = None,
    *,
    workspace_root: str = "/workspace",
) -> str:
    """容器内源码根：优先 input_json.source_path；旧的 /workspace/project 不存在时扫真实仓库名。"""
    root = Path(workspace_root)
    raw = str((input_json or {}).get("source_path") or "").strip().replace("\\", "/")
    name = ""
    if raw.startswith("/workspace/"):
        name = raw[len("/workspace/") :].strip("/")
    elif raw:
        name = raw.rstrip("/").split("/")[-1]

    candidate = (root / name) if name else None
    if candidate is not None and candidate.is_dir():
        return f"/workspace/{name}"

    discovered = _discover_workspace_repo(root)
    if discovered:
        return f"/workspace/{discovered.name}"

    if name:
        return f"/workspace/{name}"
    return "/workspace"


def _sdk_cwd(
    input_json: dict[str, Any] | None = None,
    *,
    workspace_root: str = "/workspace",
) -> str:
    """SDK cwd 必须是已存在的目录，否则 subprocess 直接炸 Working directory does not exist。"""
    root = Path(workspace_root)
    mapped = _container_source_dir(input_json, workspace_root=workspace_root)
    rel = (
        mapped[len("/workspace/") :].strip("/")
        if mapped.startswith("/workspace/")
        else ""
    )
    if rel:
        on_disk = root / rel
        if on_disk.is_dir():
            return f"/workspace/{rel}"
    discovered = _discover_workspace_repo(root)
    if discovered:
        return f"/workspace/{discovered.name}"
    if root.is_dir():
        return "/workspace"
    return mapped


def _discover_workspace_repo(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    skip = {".secrets", ".git"}
    found = [
        p
        for p in sorted(root.iterdir())
        if p.is_dir() and not p.name.startswith(".") and p.name not in skip
    ]
    return found[0] if found else None


def _build_node_prompt(node_key: str, input_json: dict[str, Any]) -> str:
    """user message：只带本轮 JSON。角色/禁令/工作流在蒸馏 skill（system_prompt）。"""
    payload = json.dumps(input_json, ensure_ascii=False, indent=2, default=str)
    return (
        "按 system 完成本节点。完成后必须调用 submit_result。\n\n"
        f"输入(JSON):\n{payload}"
    )


# ── 节点蒸馏 skill（system_prompt）──
# 生产：worker -v 挂当前节点目录 → /node-skill:ro，只读 SKILL.md。
# 单测：可设 NODE_SKILL_DIR 指向仓库 node-skills/ 做回退。
# 集合与 submit schema 同源，禁止再手抄一份（漏 api_hunt 会让猎洞走兼容 prompt、
# 且不校验 submit_result）。

from runner.node_schemas import NODE_INPUT_SCHEMAS

NODE_AI_KEYS = frozenset(NODE_INPUT_SCHEMAS)


def _load_node_skill(node_key: str) -> str:
    """加载本节点 skill 正文。优先卷映射路径，禁止依赖镜像内全量 skills。"""
    candidates: list[Path] = []
    override = os.environ.get("NODE_SKILL_FILE")
    if override:
        candidates.append(Path(override))
    candidates.append(Path("/node-skill/SKILL.md"))
    skill_dir = os.environ.get("NODE_SKILL_DIR")
    if skill_dir:
        candidates.append(Path(skill_dir) / node_key / "SKILL.md")
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"节点 skill 不存在（已查 {', '.join(str(p) for p in candidates)}）"
    )


def _system_prompt_for(node_key: str | None) -> dict[str, str] | None:
    if not node_key or node_key not in NODE_AI_KEYS:
        return None
    return {
        "type": "preset",
        "preset": "claude_code",
        "append": _load_node_skill(node_key),
    }


def _usage_jsonable(value: Any) -> Any:
    """把 SDK usage / model_usage 收成可 json.dumps 的 dict，禁止 default=str。

    ResultMessage.usage 通常已是 dict；model_usage 值为 ModelUsage TypedDict
    （运行时也是 dict）。兼容网关/未来 SDK 若给出对象，必须抽出 token 字段，
    否则 sidecar 写成字符串后台账 isinstance(dict) 丢弃 → 画像/靶场/猎洞全 0。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _usage_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_usage_jsonable(v) for v in value]
    extracted: dict[str, Any] = {}
    for key in (
        "inputTokens",
        "outputTokens",
        "cacheReadInputTokens",
        "cacheCreationInputTokens",
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "costUSD",
        "cost_usd",
    ):
        v = getattr(value, key, None)
        if v is not None:
            extracted[key] = v
    if extracted:
        return extracted
    if hasattr(value, "__dict__"):
        public = {k: v for k, v in vars(value).items() if not k.startswith("_")}
        if public:
            return _usage_jsonable(public)
    return value


# read_slice 输出边界：单次硬上限 8KB / 最多 20 处命中
_READ_SLICE_MAX_OUTPUT = 8_192
_READ_SLICE_MAX_MATCHES = 20

READ_SLICE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "目标文件路径，必须位于 /workspace 之内",
        },
        "pattern": {
            "type": "string",
            "description": "关键词或正则（Python re 语法）；给定时返回命中点 ±context 片段",
        },
        "byte_offset": {
            "type": "integer",
            "minimum": 0,
            "description": "窗口模式起始字节偏移（pattern 为空时生效）",
        },
        "byte_length": {
            "type": "integer",
            "minimum": 1,
            "description": "窗口长度（字节，上限 8192；pattern 为空时生效）",
        },
        "context": {
            "type": "integer",
            "minimum": 0,
            "maximum": 2000,
            "description": "命中点上下文字节数，默认 300",
        },
    },
    "required": ["file_path"],
}


def _read_slice_impl(
    file_path: str,
    pattern: str | None = None,
    byte_offset: int = 0,
    byte_length: int = 4096,
    context: int = 300,
    *,
    root: str = _WORKSPACE_ROOT,
) -> dict:
    """read_slice 的纯函数实现（容器外可单测）。

    pattern 模式：全文按字节正则扫描，返回命中点 ±context 片段（附 byte_offset
    供窗口模式翻页）；窗口模式：返回 [byte_offset, byte_offset+byte_length)。
    两种模式输出都受 _READ_SLICE_MAX_OUTPUT 硬上限约束。
    """
    target = Path(file_path)
    if not target.is_absolute():
        target = Path(root) / target
    target = target.resolve()
    root_resolved = Path(root).resolve()
    if root_resolved != target and root_resolved not in target.parents:
        return {"error": f"路径必须在 {root} 之下: {file_path}"}
    if not target.is_file():
        return {"error": f"文件不存在: {file_path}"}
    try:
        data = target.read_bytes()
    except OSError as e:
        return {"error": f"读取失败: {e}"}
    size = len(data)

    if pattern:
        try:
            rx = re.compile(pattern.encode("utf-8"))
        except re.error as e:
            return {"error": f"正则无效: {e}"}
        matches: list[dict] = []
        budget = _READ_SLICE_MAX_OUTPUT
        capped = False
        for m in rx.finditer(data):
            if len(matches) >= _READ_SLICE_MAX_MATCHES or budget <= 0:
                capped = True
                break
            excerpt = data[max(m.start() - context, 0): min(m.end() + context, size)]
            if len(excerpt) > budget:
                excerpt = excerpt[:budget]
                capped = True
            budget -= len(excerpt)
            matches.append(
                {
                    "byte_offset": m.start(),
                    "match": m.group(0).decode("utf-8", errors="replace"),
                    "excerpt": excerpt.decode("utf-8", errors="replace"),
                }
            )
        return {
            "file": str(target),
            "size_bytes": size,
            "capped": capped,
            "matches": matches,
        }

    byte_offset = max(int(byte_offset), 0)
    byte_length = min(max(int(byte_length), 1), _READ_SLICE_MAX_OUTPUT)
    chunk = data[byte_offset: byte_offset + byte_length]
    return {
        "file": str(target),
        "size_bytes": size,
        "byte_offset": byte_offset,
        "excerpt": chunk.decode("utf-8", errors="replace"),
        "has_more": byte_offset + byte_length < size,
    }


def _make_read_slice_tool():
    """构造 read_slice MCP 工具：对压缩产物等单行超长文件做有界读取。

    Read/Grep 对压缩产物已被 _read_guard_hook deny，本工具是官方出路：
    按命中点 ±context 或字节窗口取片段，输出有界不烧上下文。
    """
    from claude_agent_sdk import tool

    @tool(
        name="read_slice",
        description=(
            "有界读取超长单行文件（压缩/打包产物，如 *.min.js、bundle.js）的片段；"
            "Read/Grep 对这类文件会被拒绝。pattern 给定时返回最多 20 处"
            "命中点 ±context 字节的片段及 byte_offset；pattern 为空时返回"
            "byte_offset 起的 byte_length 字节窗口（可用 byte_offset 翻页）。"
            "单次输出 ≤8KB。file_path 必须位于 /workspace 之下。"
        ),
        input_schema=READ_SLICE_SCHEMA,
    )
    async def read_slice(input: dict) -> dict:
        byte_offset = input.get("byte_offset")
        byte_length = input.get("byte_length")
        context = input.get("context")
        return _read_slice_impl(
            str(input.get("file_path") or ""),
            pattern=input.get("pattern"),
            byte_offset=0 if byte_offset is None else int(byte_offset),
            byte_length=4096 if byte_length is None else int(byte_length),
            context=300 if context is None else int(context),
        )

    return read_slice


def _make_submit_result_tool(schema: dict):
    """构造 submit_result MCP 工具:agent 调用时把 input 写到 /workspace/.node_output.json。

    SDK 0.2.134 PoC 确认:create_sdk_mcp_server + @tool 原生支持自定义工具注入。
    """
    from claude_agent_sdk import tool

    @tool(
        name="submit_result",
        description="提交本节点的结构化结果。完成后必须调用此工具。",
        input_schema=schema,
    )
    async def submit_result(input: dict) -> dict:
        # input 已按 schema 校验;写文件供 worker 读取
        out_path = Path(
            os.environ.get("NODE_OUTPUT_PATH", "/workspace/.node_output.json")
        )
        out_path.write_text(
            json.dumps(input, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return {"status": "submitted", "fields": list(input.keys())}

    return submit_result


def _build_options(
    model: str,
    max_turns: int,
    node_key: str | None = None,
    cwd: str | None = None,
) -> ClaudeAgentOptions:
    """构造 SDK options。

    节点化:蒸馏 skill → system_prompt append + submit_result MCP。
    兼容(.prompt.json 且无 NODE_KEY):不加载插件、不读 skill。
    """
    common: dict[str, Any] = {
        "model": model,
        "max_turns": max_turns,
        "cwd": cwd or "/workspace",
        "tools": {"type": "preset", "preset": "claude_code"},
        "permission_mode": "bypassPermissions",
        "allowed_tools": _allowed_tools_for(node_key),
        # 待审计仓库只作为数据读取，不加载其 CLAUDE.md/.claude 配置。
        "setting_sources": [],
        # 只使用平台显式传入的 MCP，忽略项目/用户/插件 MCP。
        "strict_mcp_config": True,
        # 文件、网络与进程边界由一次性 Docker runner 负责。Provider 凭据由
        # PreToolUse 对 Bash 包 env -u 剥离（SCRUB=1/bwrap 与当前 Docker 安全配置冲突）。
        # sandbox/bwrap 关闭：外层已是一次性容器。
        #
        # 必须用 HookMatcher 实例：SDK _convert_hooks_to_internal_format 用
        # hasattr(matcher, "hooks")；裸 dict 没有该属性，会静默变成 hooks=[]，
        # 导致黑名单、凭据剥离与 Stop 催交全部不生效。
        "sandbox": {"enabled": False},
        "hooks": {
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[_pre_tool_use_hook]),
                # 压缩产物按行读必超限：deny 并指路 read_slice（详见 _read_guard_hook）
                HookMatcher(matcher="Read", hooks=[_read_guard_hook]),
                HookMatcher(matcher="Grep", hooks=[_read_guard_hook]),
            ],
            "Stop": [
                HookMatcher(hooks=[_stop_hook]),
            ],
        },
    }
    system_prompt = _system_prompt_for(node_key)
    if system_prompt is not None:
        common["system_prompt"] = system_prompt

    effort = (os.environ.get("CLAUDE_CODE_EFFORT_LEVEL") or "").strip()
    if effort and effort != "auto":
        common["effort"] = effort

    if node_key and node_key in NODE_INPUT_SCHEMAS:
        from claude_agent_sdk import create_sdk_mcp_server

        # NODE_SCHEMA_KEY 允许同一 NODE_KEY 按技能模式切换提交契约
        # （如 triage + triage_batch 子代理批量模式）；缺省回退 NODE_KEY
        schema_key = (os.environ.get("NODE_SCHEMA_KEY") or "").strip() or node_key
        schema = NODE_INPUT_SCHEMAS.get(schema_key) or NODE_INPUT_SCHEMAS[node_key]
        submit_tool = _make_submit_result_tool(schema)
        server = create_sdk_mcp_server(
            name="crucible", tools=[submit_tool, _make_read_slice_tool()]
        )
        common["mcp_servers"] = {"crucible": server}
        allowed = list(common["allowed_tools"]) + [
            "mcp__crucible__submit_result",
            "mcp__crucible__read_slice",
        ]
        if schema_key == "triage_batch":
            # 批量模式：主会话用 Task 子代理并行审议家族
            allowed.append("Task")
        common["allowed_tools"] = allowed

    # SDK 版本已在镜像中固定。关键参数不再静默降级；构造失败由顶层统一
    # 输出 agent.failed，避免 MCP/effort 被悄悄移除后继续运行。
    return ClaudeAgentOptions(**common)


# ── Main ──


async def _main() -> int:
    # 节点模式(阶段 2):优先读 .node.json;兼容模式:读 .prompt.json
    node_path = Path(os.environ.get("NODE_INPUT_PATH", "/workspace/.node.json"))
    prompt_path = Path("/workspace/.prompt.json")
    node_key = os.environ.get("NODE_KEY") or None
    input_json: dict[str, Any] = {}
    task: dict[str, Any] = {}

    if node_path.exists():
        try:
            _payload = json.loads(node_path.read_text(encoding="utf-8"))
            node_key = _payload.get("node_key") or node_key
            input_json = _payload.get("input_json", {})
            task = input_json
        except (json.JSONDecodeError, OSError) as e:
            print(
                json.dumps(
                    _failed_event(
                        f".node.json 解析失败: {e}",
                        sequence=0,
                        timestamp=time.time(),
                    ),
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 2
    elif prompt_path.exists():
        try:
            task = json.loads(prompt_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(
                json.dumps(
                    _failed_event(
                        f".prompt.json 解析失败: {e}",
                        sequence=0,
                        timestamp=time.time(),
                    ),
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 2
    else:
        print(
            json.dumps(
                _failed_event(
                    "既无 .node.json 也无 .prompt.json",
                    sequence=0,
                    timestamp=time.time(),
                ),
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 2

    model = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")
    try:
        max_turns = int(os.environ.get("CLAUDE_SDK_MAX_TURNS", "480"))
    except ValueError:
        max_turns = 480

    options = _build_options(
        model, max_turns, node_key=node_key, cwd=_sdk_cwd(input_json)
    )

    if node_key and node_key in NODE_AI_KEYS:
        prompt = _build_node_prompt(node_key, input_json)
    else:
        prompt = _build_prompt(task)

    # 审计链 sidecar：真实 prompt/skill/usage 回传 worker（spec §4.2 全量审计）
    meta: dict[str, Any] = {
        "node_key": node_key,
        "model": model,
        "prompt": prompt,
    }
    if node_key and node_key in NODE_AI_KEYS:
        try:
            meta["system_append"] = _load_node_skill(node_key)
        except (OSError, FileNotFoundError):
            meta["system_append"] = None
    assistant_texts: list[str] = []

    exit_code = 0
    saw_completion = False
    saw_failure = False
    saw_llm_failure = False
    async for event in _stream_messages(options, prompt, node_key=node_key):
        print(json.dumps(event, ensure_ascii=False, default=str), flush=True)
        et = event.get("type")
        if et == "agent.message" and event.get("text"):
            assistant_texts.append(str(event["text"]))
        if et == "agent.completed":
            saw_completion = True
            if event.get("is_error"):
                saw_failure = True
            for k in (
                "usage",
                "model_usage",
                "num_turns",
                "duration_ms",
                "total_cost_usd",
                "session_id",
            ):
                if event.get(k) is not None:
                    meta[k] = event[k]
        elif et == "agent.failed":
            saw_failure = True
            if _is_llm_api_failure(str(event.get("error") or "")):
                saw_llm_failure = True
    meta["assistant_text"] = "\n".join(assistant_texts)[-8000:]
    try:
        Path(os.environ.get("NODE_META_PATH", "/workspace/.node_meta.json")).write_text(
            json.dumps(meta, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    except OSError:
        pass  # sidecar 失败不影响节点主流程

    # 节点模式:校验 submit_result 是否被调用(.node_output.json 存在)
    if node_key and node_key in NODE_AI_KEYS:
        output_path = Path(
            os.environ.get("NODE_OUTPUT_PATH", "/workspace/.node_output.json")
        )
        if not output_path.exists() and not saw_llm_failure:
            print(
                json.dumps(
                    _failed_event(
                        f"节点 {node_key} 未调用 submit_result(无 .node_output.json)",
                        sequence=999,
                        timestamp=time.time(),
                    ),
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 1

    if saw_failure and not saw_completion:
        return 1
    if not saw_completion:
        # 容器被外力 kill 或 SDK 提前退出
        return 2
    return exit_code


if __name__ == "__main__":
    try:
        code = asyncio.run(_main())
        sys.exit(code)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        # 兜底：任何未被 _stream_messages 捕获的异常都输出失败事件
        print(
            json.dumps(
                _failed_event(
                    str(e)[:500],
                    exception=type(e).__name__,
                    sequence=0,
                    timestamp=time.time(),
                ),
                ensure_ascii=False,
            ),
            flush=True,
        )
        sys.exit(1)
