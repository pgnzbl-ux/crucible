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
                    "conclusion": _classify_conclusion(result_text),
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


def _build_node_prompt(node_key: str, input_json: dict[str, Any]) -> str:
    """按节点构造 user message。源码在容器内固定为 /workspace/project。"""
    payload = json.dumps(input_json, ensure_ascii=False, indent=2, default=str)
    headers = {
        "env_ready": (
            "你是靶场工程师。源码在 /workspace/project。"
            "根据以下输入产出 Dockerfile / docker-compose.yml 到 "
            "/workspace/project/.vuln-env/，完成后必须调用 submit_result。"
            "不要自己执行 docker compose（由平台 worker 执行）。"
        ),
        "audit": (
            "你是白盒审计员。源码在 /workspace/project。"
            "根据以下输入完成利用链审计与 Phase 2.5 Gate，完成后必须调用 submit_result。"
        ),
        "reproduce": (
            "你是漏洞复现员。根据以下输入对靶标发一次 HTTP 验证，完成后必须调用 submit_result。"
            "target_url 中的 host.docker.internal 指向宿主机上的靶场（不要改用 localhost）。"
        ),
        "report": (
            "你是报告撰写员。根据以下全部前序输出生成 8 节 report_data，"
            "完成后必须调用 submit_result。"
        ),
    }
    head = headers.get(
        node_key,
        "根据以下输入完成节点任务，完成后必须调用 submit_result。",
    )
    return f"{head}\n\n源码目录: /workspace/project\n\n输入(JSON):\n{payload}"


# ── 插件 agent 加载 ──

# 容器内插件路径（Dockerfile ENV 注入，可被 docker run --env 覆盖）
PLUGIN_DIR = os.environ.get("PLUGIN_DIR", "/app/plugins/vuln-verify-expert")
PLUGIN_NAME = os.environ.get("PLUGIN_NAME", "vuln-verify-expert")
AGENT_NAME = os.environ.get("AGENT_NAME", "vuln-verify-expert")

# 节点 → 插件 agent 映射(阶段 2 多 agent 拆分)
# NODE_KEY 由 worker 通过 docker run --env 注入;为空时走旧的 vuln-verify-expert 总 agent(兼容)
NODE_AGENT_MAP: dict[str, str] = {
    "env_ready": "env-builder",
    "audit": "auditor",
    "reproduce": "reproducer",
    "report": "reporter",
}

# 各 AI 节点的 submit_result 工具 input schema(与 worker 侧 ai_runner.NODE_INPUT_SCHEMAS 对齐)
NODE_INPUT_SCHEMAS: dict[str, dict] = {
    "env_ready": {
        "type": "object",
        "properties": {
            "target_url": {"type": "string"},
            "compose_path": {"type": "string"},
            "transport_shape": {"type": "object"},
            "initial_creds": {"type": "object"},
            "started_containers": {"type": "array"},
        },
        "required": ["target_url", "compose_path"],
    },
    "audit": {
        "type": "object",
        "properties": {
            "kill_chain": {"type": "string"},
            "defense_layers": {"type": "array"},
            "payloads": {"type": "array"},
            "gate_verdict": {"type": "string", "enum": ["pass", "fail"]},
            "gate_reason": {"type": "string"},
        },
        "required": ["gate_verdict"],
    },
    "reproduce": {
        "type": "object",
        "properties": {
            "reproduced": {"type": "boolean"},
            "evidence": {"type": "array"},
            "screenshots": {"type": "array"},
            "verdict": {"type": "string", "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"]},
        },
        "required": ["verdict"],
    },
    "report": {
        "type": "object",
        "properties": {
            "report_data": {"type": "object"},
            "final_verdict": {"type": "string", "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"]},
            "cvss": {"type": "object"},
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


def _build_options(model: str, max_turns: int, node_key: str | None = None) -> ClaudeAgentOptions:
    """构造 SDK options。

    节点化(node_key 非空):按 node_key 选插件子 agent + 注入 submit_result MCP 工具。
    兼容模式(node_key 空):走旧的 vuln-verify-expert 总 agent(单次大调用)。
    """
    # 选 agent
    if node_key and node_key in NODE_AGENT_MAP:
        agent_name = NODE_AGENT_MAP[node_key]
    else:
        agent_name = AGENT_NAME
    agent_flag = f"{PLUGIN_NAME}:{agent_name}"

    common: dict[str, Any] = {
        "model": model,
        "max_turns": max_turns,
        "cwd": "/workspace/project",
        "permission_mode": "bypassPermissions",
        "allowed_tools": [
            "Read", "Grep", "Glob", "Bash",         # 白盒审计 + PoC
            "Write", "Edit",                         # 写配置 / report
            "WebFetch", "WebSearch",                 # 查 CVE / 利用资料
        ],
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [_pre_tool_use_hook]},
            ],
        },
        "extra_args": ["--agent", agent_flag],
    }

    # 节点化:注入 submit_result MCP 工具 + 限定工具集含它
    if node_key and node_key in NODE_INPUT_SCHEMAS:
        try:
            from claude_agent_sdk import create_sdk_mcp_server

            schema = NODE_INPUT_SCHEMAS[node_key]
            submit_tool = _make_submit_result_tool(schema)
            server = create_sdk_mcp_server(name="crucible", tools=[submit_tool])
            common["mcp_servers"] = {"crucible": server}
            common["allowed_tools"] = common["allowed_tools"] + ["submit_result"]
        except (ImportError, TypeError, Exception) as e:  # noqa: BLE001
            # MCP 注入失败:降级(prompt 要求 ```json 块,可靠性降级但不阻塞)
            print(json.dumps({
                "type": "agent.warning",
                "message": f"submit_result MCP 注入失败,降级文本模式: {type(e).__name__}: {e}",
                "timestamp": time.time(),
            }, ensure_ascii=False), flush=True)

    # 插件加载
    try:
        return ClaudeAgentOptions(
            plugins=[{"type": "local", "path": PLUGIN_DIR}],
            **common,
        )
    except (TypeError, ValueError):
        common["extra_args"] = ["--plugin-dir", PLUGIN_DIR, "--agent", agent_flag]
        # mcp_servers 可能不被旧构造接受,移除后重试
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

    options = _build_options(model, max_turns, node_key=node_key)

    if node_key and node_key in NODE_AGENT_MAP:
        prompt = _build_node_prompt(node_key, input_json)
    else:
        prompt = _build_prompt(task)

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

    # 节点模式:校验 submit_result 是否被调用(.node_output.json 存在)
    if node_key and node_key in NODE_AGENT_MAP:
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