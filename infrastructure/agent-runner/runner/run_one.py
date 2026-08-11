"""
agent-runner 容器内 entrypoint。

职责：读取 /workspace/.prompt.json → 构造 ClaudeAgentOptions → 调用 query() →
       逐条翻译 SDK Message 为统一事件结构 → 写到 stdout（JSONL 一行一条）。

环境变量由 worker 在 docker run 时通过 --env 注入：
  ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY / ANTHROPIC_MODEL
  ANTHROPIC_SMALL_FAST_MODEL / ANTHROPIC_DEFAULT_HAIKU_MODEL
  API_TIMEOUT_MS / CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC / PYTHONUNBUFFERED=1

退出码：
  0  = 正常完成（含 conclusion=unconfirmed 等业务软失败）
  1  = 业务失败（LLM error / 无产出）
  2  = 基础设施错误（OOM / 网络断开 / 凭据缺失）
  137 = SIGKILL（被 worker revoke）

设计要点：
- SDK 在容器内解析 Message → 翻译为 dict → json.dumps 到 stdout；
  worker 侧只 json.loads 每行，不感知 SDK 类型。
- canUseTool 回调实现白/黑名单（白盒审计：只读 + curl + git-read + python PoC），
  拒绝时输出 tool.call.denied 事件，便于事后审计。
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
from pathlib import Path
from typing import Any, AsyncIterator

try:
    from claude_agent_sdk import (
        query,
        ClaudeAgentOptions,
        AssistantMessage,
        UserMessage,
        SystemMessage,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
    )
except ImportError as e:  # 镜像构建失败时给出明确报错
    print(json.dumps({
        "type": "agent.failed",
        "error": f"claude_agent_sdk 导入失败: {e}",
        "exception": type(e).__name__,
        "sequence": 0,
        "timestamp": time.time(),
    }), file=sys.stdout, flush=True)
    sys.exit(2)


# ── Bash 黑名单（核心安全规则，PreToolUse hook 消费） ──
#
# 策略（v0.2）：黑名单 deny + 工具白名单（allowed_tools）双层模型。
# - Bash：黑名单拦截破坏性命令（rm/mv/chmod/dd/mkfs/|bash/>/etc//proc//sys），
#   其余放开（插件工作流需要 docker compose / git / curl / python）
# - 工具类型白名单：allowed_tools 限定（Write/Edit/WebFetch 等显式列出）
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


def _classify_bash(cmd: str) -> tuple[str, str | None]:
    """返回 (decision, reason)：黑名单 deny，其余 allow（放开 Bash 给插件工作流）。"""
    for pat, name in BLACKLIST_RES:
        if pat.search(cmd):
            return ("deny", f"blocked by policy: {name}")
    return ("allow", None)


async def _pre_tool_use_hook(hook_input: Any, _tool_use_id: str | None, _ctx: Any) -> dict | None:
    """PreToolUse hook：Bash 黑名单拦截，其余放行。

    SDK 0.2.x 的 hooks 字段直接接受 async 回调（非 shell command），返回
    SyncHookJSONOutput。permissionDecision 由 _bundled/claude CLI 原生消费，
    在所有 permission_mode（含 bypassPermissions）下都生效。

    matcher 限定 Bash，故本回调只处理 Bash；Write/Edit/WebFetch 等不触发。
    """
    # hook_input 是 PreToolUseHookInput（TypedDict），按字段取
    tool_name = (hook_input.get("tool_name") if isinstance(hook_input, dict) else getattr(hook_input, "tool_name", "")) or ""
    tool_input = hook_input.get("tool_input") if isinstance(hook_input, dict) else getattr(hook_input, "tool_input", {})
    if tool_name != "Bash":
        return None  # 非 Bash 不处理（matcher 已限定，兜底）

    cmd = tool_input.get("command", "") if isinstance(tool_input, dict) else str(tool_input)
    decision, reason = _classify_bash(cmd)
    if decision == "deny":
        # 审计事件（worker 侧落 AgentEvent，前端可见）
        print(json.dumps({
            "type": "tool.call.denied",
            "tool": tool_name,
            "reason": reason,
            "input": cmd[:200],
            "timestamp": time.time(),
        }, ensure_ascii=False), flush=True)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason or "denied by policy",
            }
        }
    # allow：返回 None 走默认流程（bypassPermissions 自动批准）
    return None


# ── SDK Message → 统一事件结构翻译 ──

def _safe_get(obj: Any, *path: str, default: Any = None) -> Any:
    """嵌套字段安全访问（兼容 SDK 不同版本的属性差异）"""
    cur = obj
    for key in path:
        try:
            cur = getattr(cur, key, None) or (cur.get(key) if isinstance(cur, dict) else None)
        except (AttributeError, KeyError):
            return default
        if cur is None:
            return default
    return cur


def _truncate(text: Any, limit: int) -> str:
    if not isinstance(text, str):
        text = str(text)
    return text[:limit]


def _classify_conclusion(text: str) -> str:
    """文本匹配：exists / not_exists / unconfirmed（与 executor.py 同语义）"""
    if not text:
        return "unconfirmed"
    lowered = text.lower()
    if any(k in lowered for k in ("漏洞存在", "确认存在", "reproduced", "confirmed", "vulnerable",
                                    "is exploitable", "结论：存在", "存在漏洞")):
        return "exists"
    if any(k in lowered for k in ("不存在", "无法确认", "not vulnerable", "not exploitable",
                                    "unconfirmed", "结论：不存在", "误报")):
        return "not_exists"
    return "unconfirmed"


async def _stream_messages(options: ClaudeAgentOptions, prompt: str) -> AsyncIterator[dict]:
    """包裹 SDK async generator，把每条 Message 翻译为 dict。"""
    seq = 0
    session_id_seen: str | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            seq += 1
            ts = time.time()
            sid = getattr(message, "session_id", None) or session_id_seen
            message_type = type(message).__name__

            # SystemMessage（init）
            if isinstance(message, SystemMessage):
                if sid and sid != session_id_seen:
                    session_id_seen = sid
                yield {
                    "type": "phase.updated",
                    "phase": "start",
                    "message": getattr(message, "subtype", "init") or "init",
                    "session_id": sid,
                    "sequence": seq,
                    "timestamp": ts,
                }
                continue

            # AssistantMessage（含 TextBlock / ToolUseBlock / 错误）
            if isinstance(message, AssistantMessage):
                # 错误分支
                err = getattr(message, "error", None)
                if err:
                    err_msg = getattr(err, "message", str(err))
                    yield {
                        "type": "agent.failed",
                        "error": _truncate(err_msg, 500),
                        "model": getattr(message, "model", None),
                        "session_id": sid,
                        "sequence": seq,
                        "timestamp": ts,
                    }
                    continue

                content = getattr(message, "content", None) or []
                if not isinstance(content, list):
                    content = [content]

                for block in content:
                    if isinstance(block, TextBlock):
                        yield {
                            "type": "agent.message",
                            "text": getattr(block, "text", "") or "",
                            "model": getattr(message, "model", None),
                            "session_id": sid,
                            "sequence": seq,
                            "timestamp": ts,
                        }
                    elif isinstance(block, ToolUseBlock):
                        yield {
                            "type": "tool.call.started",
                            "tool": getattr(block, "name", "unknown"),
                            "input": getattr(block, "input", {}) or {},
                            "tool_use_id": getattr(block, "id", None),
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
                        # content 可能是 str 或 list（list of text/image blocks）
                        raw_content = getattr(block, "content", "") or ""
                        if isinstance(raw_content, list):
                            raw_content = " ".join(
                                getattr(b, "text", "") for b in raw_content if hasattr(b, "text")
                            )
                        yield {
                            "type": "tool.call.completed",
                            "tool_use_id": getattr(block, "tool_use_id", None),
                            "output": _truncate(raw_content, 2000),
                            "is_error": bool(getattr(block, "is_error", False)),
                            "session_id": sid,
                            "sequence": seq,
                            "timestamp": ts,
                        }
                continue

            # ResultMessage（终态）
            if isinstance(message, ResultMessage):
                result_text = getattr(message, "result", "") or ""
                yield {
                    "type": "agent.completed",
                    "conclusion": _classify_conclusion(result_text),
                    "reasoning": result_text,
                    "session_id": sid,
                    "duration_ms": getattr(message, "duration_ms", None),
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "num_turns": getattr(message, "num_turns", None),
                    "is_error": bool(getattr(message, "is_error", False)),
                    "sequence": seq,
                    "timestamp": ts,
                }
                continue

            # 未知 Message 类型：原样序列化（兜底，page-ui 可能忽略）
            yield {
                "type": "raw.message",
                "message_type": message_type,
                "session_id": sid,
                "raw": _truncate(str(message), 500),
                "sequence": seq,
                "timestamp": ts,
            }

    except Exception as e:
        seq += 1
        yield {
            "type": "agent.failed",
            "error": _truncate(str(e), 500),
            "exception": type(e).__name__,
            "traceback": _truncate(traceback.format_exc(), 1000),
            "sequence": seq,
            "timestamp": time.time(),
        }
        raise


# ── Prompt 构造 ──
#
# 插件 agent（vuln-verify-expert）自带 system prompt（agents/vuln-verify-expert.md 的
# frontmatter + 正文），这里不再拼 SYSTEM_PROMPT —— 传给 query() 的只是任务本身的
# user message。agent 的阶段化工作流（阶段 0 平台预检 / A 接单建仓 / B 搭靶场 /
# C 漏洞验证 / D 交付收尾）与 skills（run-project-env / vuln-verify）由插件自动加载。


def _build_prompt(task: dict[str, Any]) -> str:
    """构造发给插件 agent 的 user message（只含任务信息，不含 system prompt）。"""
    parts = [
        f"项目地址: {task.get('project_address', '')}",
        f"项目引用: {task.get('project_ref') or 'default branch'}（已 clone 到 /workspace/project）",
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
            parts.append("- 环境变量: " + ", ".join(s.get("target", "?") for s in envcreds))
        if filecreds:
            parts.append("- 密钥文件（容器内路径，权限 600）:")
            for s in filecreds:
                desc = f" ({s['description']})" if s.get("description") else ""
                parts.append(f"    {s.get('path', '?')}{desc}")

    parts.append("")
    parts.append("请按你的工作流（阶段 A→B→C→D）验证上述漏洞是否真实存在，并用 phase.updated "
                 "事件记录每个阶段进度，最终产出中文报告。")
    return "\n".join(parts)


# ── 插件 agent 加载 ──

# 容器内插件路径（Dockerfile ENV 注入，可被 docker run --env 覆盖）
PLUGIN_DIR = os.environ.get("PLUGIN_DIR", "/app/plugins/vuln-verify-expert")
PLUGIN_NAME = os.environ.get("PLUGIN_NAME", "vuln-verify-expert")
AGENT_NAME = os.environ.get("AGENT_NAME", "vuln-verify-expert")


def _build_options(model: str, max_turns: int) -> ClaudeAgentOptions:
    """构造 SDK options：加载 vuln-verify-expert 插件 + 指定其 agent。

    - 插件 agent 的 system_prompt / skills 由 agents/*.md 自动提供，这里**不**传 system_prompt
    - extra_args 透传 --agent（SDK 无 typed `agent` 字段，CLI flag 是唯一方式，格式 plugin:agent）
    - 插件加载优先 typed `plugins=`，降级 `extra_args --plugin-dir`（跨 SDK 版本兼容）
    - 凭据零落盘：插件只含静态 .md，密钥通过 worker 注入的 ANTHROPIC_* env 提供
    """
    agent_flag = f"{PLUGIN_NAME}:{AGENT_NAME}"

    common: dict[str, Any] = {
        "model": model,
        "max_turns": max_turns,
        "cwd": "/workspace/project",
        # bypassPermissions：非 Bash 工具自动批准（减少摩擦）。
        # Bash 的安全护栏由 PreToolUse hook 负责（黑名单 deny），hook 在所有
        # permission_mode 下都执行，不被 bypassPermissions shadow（区别于 can_use_tool）。
        # 详见 docs/agent-workflow.md §权限策略。
        "permission_mode": "bypassPermissions",
        "allowed_tools": [
            "Read", "Grep", "Glob", "Bash",         # 白盒审计 + PoC
            "Write", "Edit",                         # 写 report.md / .vuln-env.json
            "WebFetch", "WebSearch",                 # 查 CVE / 利用资料
        ],
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Bash",               # 只对 Bash 触发（其余工具不需拦截）
                    "hooks": [_pre_tool_use_hook],
                },
            ],
        },
        "extra_args": ["--agent", agent_flag],
    }

    # 插件加载：typed plugins= 优先（SDK 0.2.x，SdkPluginConfig TypedDict）
    try:
        return ClaudeAgentOptions(
            plugins=[{"type": "local", "path": PLUGIN_DIR}],
            **common,
        )
    except (TypeError, ValueError):
        # 降级：extra_args 透传 --plugin-dir（CLI flag 逃生舱，所有版本支持）
        common["extra_args"] = ["--plugin-dir", PLUGIN_DIR, "--agent", agent_flag]
        return ClaudeAgentOptions(**common)


# ── Main ──

async def _main() -> int:
    prompt_path = Path("/workspace/.prompt.json")
    if not prompt_path.exists():
        print(json.dumps({
            "type": "agent.failed",
            "error": ".prompt.json not found at /workspace",
            "sequence": 0,
            "timestamp": time.time(),
        }), ensure_ascii=False, flush=True)
        return 2

    try:
        task = json.loads(prompt_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(json.dumps({
            "type": "agent.failed",
            "error": f".prompt.json 解析失败: {e}",
            "sequence": 0,
            "timestamp": time.time(),
        }), ensure_ascii=False, flush=True)
        return 2

    model = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")
    try:
        max_turns = int(os.environ.get("CLAUDE_SDK_MAX_TURNS", "180"))
    except ValueError:
        max_turns = 180

    options = _build_options(model, max_turns)

    prompt = _build_prompt(task)

    # 流式输出
    exit_code = 0
    saw_completion = False
    saw_failure = False
    async for event in _stream_messages(options, prompt):
        print(json.dumps(event, ensure_ascii=False, default=str), flush=True)
        if event.get("type") == "agent.completed":
            saw_completion = True
            if event.get("is_error"):
                saw_failure = True
        elif event.get("type") == "agent.failed":
            saw_failure = True

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
    except BaseException as e:
        # 兜底：任何未被 _stream_messages 捕获的异常都输出失败事件
        print(json.dumps({
            "type": "agent.failed",
            "error": str(e)[:500],
            "exception": type(e).__name__,
            "sequence": 0,
            "timestamp": time.time(),
        }), ensure_ascii=False, flush=True)
        sys.exit(1)