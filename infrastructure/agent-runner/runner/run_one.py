"""
agent-runner 容器内 entrypoint。

职责：读取 /workspace/.node.json（节点模式）或 .prompt.json（兼容）→
       按 NODE_KEY 加载蒸馏 skill 作 system_prompt → 调用 query() →
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
        "title": "容器内缺少 Claude Agent SDK",
        "hint": "agent-runner 镜像不完整，请重新构建镜像。",
        "exception": type(e).__name__,
        "sequence": 0,
        "timestamp": time.time(),
    }), file=sys.stdout, flush=True)
    sys.exit(2)

try:
    from claude_agent_sdk import ThinkingBlock  # type: ignore[attr-defined]
except ImportError:
    ThinkingBlock = None  # type: ignore[misc, assignment]


# ── Bash 黑名单（核心安全规则，PreToolUse hook 消费） ──
#
# 策略（v0.2）：黑名单 deny + 工具白名单（allowed_tools）双层模型。
# - Bash：黑名单拦截破坏性命令（rm/mv/chmod/dd/mkfs/|bash/>/etc//proc//sys），
#   其余放开（插件工作流需要 git / curl / python / node / 常规 Linux 命令）
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
    (re.compile(r"\bdocker\b"), "docker"),
]

# audit 额外拒绝：HTTP 客户端（白盒节点禁止打活靶；不拦 python urllib）
AUDIT_DENY_RES = [
    (re.compile(r"\bcurl\b"), "curl"),
    (re.compile(r"\bwget\b"), "wget"),
    (re.compile(r"\bhttpie\b"), "httpie"),
]

# reproduce 额外拒绝：靶场已由平台 compose 启动，禁止 Agent 自己 docker
REPRODUCE_DENY_RES = [
    (re.compile(r"\bdocker\b"), "docker"),
]


def _allowed_tools_for(node_key: str | None) -> list[str]:
    if node_key == "profile":
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
    decision, reason = _classify_bash(cmd, os.environ.get("NODE_KEY"))
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
    rules = [
        ("未调用 submit_result", "Agent 没有提交节点结果就结束了", "模型未调用 submit_result。检查节点 prompt 或 MCP 工具注入。"),
        ("claude_agent_sdk 导入失败", "容器内缺少 Claude Agent SDK", "重新构建 agent-runner 镜像。"),
        ("NameError", "容器入口代码异常", "更新 run_one.py 后必须重建镜像。"),
        ("Authentication", "LLM 鉴权失败", "检查 API Key 与 Base URL。"),
        ("401", "LLM 接口拒绝访问（401）", "API Key 无效或未注入容器。"),
        (".node.json 解析失败", "节点输入文件损坏", "worker 写入的 .node.json 不是合法 JSON。"),
        ("既无 .node.json 也无 .prompt.json", "容器没拿到任务输入", "检查 host_workdir 是否正确 bind mount 到 /workspace。"),
    ]
    for needle, title, hint in rules:
        if needle.lower() in text.lower():
            return title, hint
    return text[:240], "查看本条事件的原文与 traceback，对照失败发生在思考、工具还是收尾。"


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


# SDK SystemMessage 里 thinking_tokens 是逐 token 用量心跳，不是阶段变更
_KEEP_SYSTEM_SUBTYPES = frozenset({"init"})


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
    return {
        "type": "phase.updated",
        "phase": "start",
        "message": subtype,
        "session_id": session_id,
        "sequence": seq,
        "timestamp": timestamp,
    }


async def _stream_messages(
    options: ClaudeAgentOptions,
    prompt: str,
    node_key: str | None = None,
) -> AsyncIterator[dict]:
    """包裹 SDK async generator，把每条 Message 翻译为 dict。"""
    seq = 0
    session_id_seen: str | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            seq += 1
            ts = time.time()
            sid = getattr(message, "session_id", None) or session_id_seen
            message_type = type(message).__name__

            # SystemMessage：只保留 init；thinking_tokens 等用量心跳丢弃
            if isinstance(message, SystemMessage):
                if sid and sid != session_id_seen:
                    session_id_seen = sid
                event = _system_phase_event(message, seq=seq, timestamp=ts, session_id=sid)
                if event:
                    yield event
                continue

            # AssistantMessage（含 TextBlock / ToolUseBlock / 错误）
            if isinstance(message, AssistantMessage):
                # 错误分支
                err = getattr(message, "error", None)
                if err:
                    err_msg = getattr(err, "message", str(err))
                    yield _failed_event(
                        err_msg,
                        model=getattr(message, "model", None),
                        session_id=sid,
                        sequence=seq,
                        timestamp=ts,
                    )
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
                            "session_id": sid,
                            "sequence": seq,
                            "timestamp": ts,
                        }
                        continue
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
                is_error = bool(getattr(message, "is_error", False))
                if is_error:
                    yield _failed_event(
                        result_text or "SDK ResultMessage.is_error=true",
                        session_id=sid,
                        sequence=seq,
                        timestamp=ts,
                    )
                yield {
                    "type": "agent.completed",
                    **({"conclusion": _classify_conclusion(result_text)} if not node_key else {}),
                    "reasoning": result_text,
                    "session_id": sid,
                    "duration_ms": getattr(message, "duration_ms", None),
                    "total_cost_usd": getattr(message, "total_cost_usd", None),
                    "num_turns": getattr(message, "num_turns", None),
                    "is_error": is_error,
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
        name = raw[len("/workspace/"):].strip("/")
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
    rel = mapped[len("/workspace/"):].strip("/") if mapped.startswith("/workspace/") else ""
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
        p for p in sorted(root.iterdir())
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

NODE_AI_KEYS = frozenset({"profile", "env_ready", "audit", "reproduce", "report"})


def _node_skill_dir() -> Path:
    override = os.environ.get("NODE_SKILL_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "node-skills"


def _load_node_skill(node_key: str) -> str:
    path = _node_skill_dir() / node_key / "SKILL.md"
    if not path.is_file():
        raise FileNotFoundError(f"节点 skill 不存在: {path}")
    return path.read_text(encoding="utf-8")


def _system_prompt_for(node_key: str | None) -> dict[str, str] | None:
    if not node_key or node_key not in NODE_AI_KEYS:
        return None
    return {
        "type": "preset",
        "preset": "claude_code",
        "append": _load_node_skill(node_key),
    }

# 各 AI 节点的 submit_result 工具 input schema(与 worker 侧 ai_runner.NODE_INPUT_SCHEMAS 对齐)
NODE_INPUT_SCHEMAS: dict[str, dict] = {
    "profile": {
        "type": "object",
        "properties": {
            "is_web": {"type": "boolean"},
            "language": {"type": "string"},
            "framework": {"type": "string"},
            "port": {"type": "integer"},
            "has_dockerfile": {"type": "boolean"},
            "has_compose": {"type": "boolean"},
            "detected_services": {"type": "array"},
            "summary": {"type": "string"},
            "start_command": {"type": "string"},
            "non_web_reason": {"type": "string"},
        },
        "required": ["is_web"],
    },
    "env_ready": {
        "type": "object",
        "properties": {
            "target_url": {"type": "string"},
            "compose_path": {"type": "string"},
            "transport_shape": {"type": "object"},
            "initial_creds": {
                "type": "object",
                "description": "实际可用的已有/靶场初始化账号；确认无登录功能写 {auth_required: false}；有登录但无法自动提供凭据写 {note: ...}",
                "properties": {
                    "username": {"type": "string", "minLength": 1},
                    "password": {"type": "string", "minLength": 1},
                    "login_url": {"type": "string"},
                    "auth_required": {"type": "boolean", "enum": [False]},
                    "note": {"type": "string", "minLength": 1},
                },
                "anyOf": [
                    {"required": ["username", "password"]},
                    {"required": ["auth_required"]},
                    {"required": ["note"]},
                ],
            },
            "started_containers": {"type": "array"},
        },
        "required": ["target_url", "compose_path", "initial_creds"],
    },
    "audit": {
        "type": "object",
        "properties": {
            "kill_chain": {"type": "string", "description": "entry→sink 完整调用链"},
            "defense_layers": {"type": "array", "description": "每层防御 + 是否 bypass"},
            "payloads": {
                "type": "array",
                "description": "HTTP 请求模板候选",
                "items": {
                    "type": "object",
                    "properties": {
                        "method": {"type": "string", "description": "HTTP 方法"},
                        "path": {"type": "string", "description": "必须以 / 开头的路径"},
                        "expected_observable": {"type": "string", "description": "预期可观察危害"},
                        "headers": {"type": "object", "description": "可选请求头"},
                        "body": {"type": "string", "description": "可选请求体"},
                        "content_type": {"type": "string", "description": "可选 Content-Type"},
                    },
                    "required": ["method", "path", "expected_observable"],
                },
            },
            "gate_verdict": {
                "type": "string",
                "enum": ["pass", "fail", "uncertain"],
                "description": "Phase 2.5 三问结论:纸上通/runtime-dependent=pass;结构性阻断=fail;描述对不上/锁不住 harm=uncertain",
            },
            "gate_reason": {"type": "string", "description": "三问各一句或阻断/待复核原因"},
            "runtime_dependent": {"type": "boolean"},
            "core_claim": {"type": "string", "description": "一条 HTTP 可观察危害"},
            "unresolved_facts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "runtime_dependent 时缺的运行时事实",
            },
        },
        "required": ["gate_verdict", "gate_reason"],
    },
    "reproduce": {
        "type": "object",
        "properties": {
            "reproduced": {"type": "boolean"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "minLength": 1},
                        "detail": {"type": "string", "minLength": 1},
                    },
                    "required": ["type", "detail"],
                },
            },
            "attempts": {"type": "array", "description": "每次 HTTP 尝试的结构化记录"},
            "screenshots": {"type": "array"},
            "verdict": {"type": "string", "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"]},
            "cvss": {"type": "object", "description": "仅 confirmed/partial 的 CVSS"},
            "vulnerable_file": {"type": "string", "description": "漏洞文件定位"},
            "poc": {
                "type": "object",
                "description": "仅 confirmed/partial 的完整 PoC",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "bash", "other"]},
                    "filename": {"type": "string"},
                    "code": {"type": "string"},
                    "usage": {"type": "string"},
                    "language_reason": {"type": "string"},
                },
            },
        },
        "required": ["verdict", "reproduced", "attempts"],
    },
    "report": {
        "type": "object",
        "properties": {
            "report_data": {"type": "object", "description": "document_kind + 8 节 Markdown"},
            "final_verdict": {"type": "string", "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"]},
            "cvss": {"type": "object", "description": "仅漏洞报告的 CVSS"},
            "vulnerable_file": {"type": "string"},
        },
        "required": ["report_data", "final_verdict"],
    },
}


def _make_submit_result_tool(schema: dict):
    """构造 submit_result MCP 工具:agent 调用时把 input 写到 /workspace/.node_output.json。

    SDK 0.2.134 PoC 确认:create_sdk_mcp_server + @tool 原生支持自定义工具注入。
    """
    from claude_agent_sdk import tool

    @tool(name="submit_result", description="提交本节点的结构化结果。完成后必须调用此工具。", input_schema=schema)
    async def submit_result(input: dict) -> dict:
        # input 已按 schema 校验;写文件供 worker 读取
        out_path = Path("/workspace/.node_output.json")
        out_path.write_text(json.dumps(input, ensure_ascii=False, default=str), encoding="utf-8")
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
        "permission_mode": "bypassPermissions",
        "allowed_tools": _allowed_tools_for(node_key),
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_pre_tool_use_hook]},
            ],
        },
    }
    system_prompt = _system_prompt_for(node_key)
    if system_prompt is not None:
        common["system_prompt"] = system_prompt

    if node_key and node_key in NODE_INPUT_SCHEMAS:
        try:
            from claude_agent_sdk import create_sdk_mcp_server

            schema = NODE_INPUT_SCHEMAS[node_key]
            submit_tool = _make_submit_result_tool(schema)
            server = create_sdk_mcp_server(name="crucible", tools=[submit_tool])
            common["mcp_servers"] = {"crucible": server}
            common["allowed_tools"] = common["allowed_tools"] + ["submit_result"]
        except (ImportError, TypeError, Exception) as e:  # noqa: BLE001
            print(json.dumps({
                "type": "agent.warning",
                "message": f"submit_result MCP 注入失败,降级文本模式: {type(e).__name__}: {e}",
                "timestamp": time.time(),
            }, ensure_ascii=False), flush=True)

    try:
        return ClaudeAgentOptions(**common)
    except (TypeError, ValueError):
        # 旧 SDK 可能不接受 system_prompt dict / mcp_servers
        if isinstance(common.get("system_prompt"), dict):
            common["system_prompt"] = common["system_prompt"].get("append") or ""
        common.pop("mcp_servers", None)
        return ClaudeAgentOptions(**common)


# ── Main ──

async def _main() -> int:
    # 节点模式(阶段 2):优先读 .node.json;兼容模式:读 .prompt.json
    node_path = Path("/workspace/.node.json")
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
            print(json.dumps(_failed_event(
                f".node.json 解析失败: {e}", sequence=0, timestamp=time.time(),
            ), ensure_ascii=False), flush=True)
            return 2
    elif prompt_path.exists():
        try:
            task = json.loads(prompt_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(json.dumps(_failed_event(
                f".prompt.json 解析失败: {e}", sequence=0, timestamp=time.time(),
            ), ensure_ascii=False), flush=True)
            return 2
    else:
        print(json.dumps(_failed_event(
            "既无 .node.json 也无 .prompt.json", sequence=0, timestamp=time.time(),
        ), ensure_ascii=False), flush=True)
        return 2

    model = os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash")
    try:
        max_turns = int(os.environ.get("CLAUDE_SDK_MAX_TURNS", "180"))
    except ValueError:
        max_turns = 180

    options = _build_options(
        model, max_turns, node_key=node_key, cwd=_sdk_cwd(input_json)
    )

    if node_key and node_key in NODE_AI_KEYS:
        prompt = _build_node_prompt(node_key, input_json)
    else:
        prompt = _build_prompt(task)

    exit_code = 0
    saw_completion = False
    saw_failure = False
    async for event in _stream_messages(options, prompt, node_key=node_key):
        print(json.dumps(event, ensure_ascii=False, default=str), flush=True)
        if event.get("type") == "agent.completed":
            saw_completion = True
            if event.get("is_error"):
                saw_failure = True
        elif event.get("type") == "agent.failed":
            saw_failure = True

    # 节点模式:校验 submit_result 是否被调用(.node_output.json 存在)
    if node_key and node_key in NODE_AI_KEYS:
        if not Path("/workspace/.node_output.json").exists():
            print(json.dumps(_failed_event(
                f"节点 {node_key} 未调用 submit_result(无 .node_output.json)",
                sequence=999,
                timestamp=time.time(),
            ), ensure_ascii=False), flush=True)
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
    except BaseException as e:
        # 兜底：任何未被 _stream_messages 捕获的异常都输出失败事件
        print(json.dumps(_failed_event(
            str(e)[:500],
            exception=type(e).__name__,
            sequence=0,
            timestamp=time.time(),
        ), ensure_ascii=False), flush=True)
        sys.exit(1)