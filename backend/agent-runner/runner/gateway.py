"""Claude Agent SDK 执行网关 — runner 内唯一的 SDK 交互归属。

输入 AgentSpec（契约全部由 backend 下发）→ 消费 query() 消息流 → 翻译为扁平
事件 dict（与 stdout JSONL 时代同构）→ 产出给 server(SSE) 或 CLI(stdout)。

结构：
- 翻译层：SystemMessage/AssistantMessage/UserMessage/ResultMessage → 事件
  （纯函数，不含 sequence/timestamp，由出口统一打号）。
- 出口：asyncio.Queue 单出口 —— hook 审计事件与消息翻译事件同队合流，
  sequence 单调递增，逐事件写 transcript。
- 收尾：退出码策略 + runner.exit 终帧 + .node_meta.json sidecar。

业务红线：本模块不含任何业务节点知识（节点清单/schema/prompt 模板/业务错误
语义全部不在）；spec 提供什么，就执行什么。
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from runner.errors import ErrorCategory, classify_error
from runner.policies import build_policy_hooks
from runner.schemas import RUNNER_EXIT_EVENT, AgentSpec
from runner.tools import make_read_slice_tool, make_submit_result_tool
from runner.transcript import (
    TranscriptWriter,
    build_run_meta,
    env_output_path,
    env_transcript_path,
    finalize_run_meta,
    write_meta_sidecar,
)

try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        SystemMessage,
        TextBlock,
        ToolResultBlock,
        ToolUseBlock,
        UserMessage,
        query,
    )
    SDK_IMPORT_ERROR: str | None = None
except ImportError as _e:  # 镜像构建失败时给出明确报错
    SDK_IMPORT_ERROR = f"claude_agent_sdk 导入失败: {_e}"

try:
    from claude_agent_sdk import ThinkingBlock  # type: ignore[attr-defined]
except ImportError:
    ThinkingBlock = None  # type: ignore[misc, assignment]

# claude_code preset 基础工具；spec.allowed_tools_extra 追加（如 Task 子代理）
BASE_ALLOWED_TOOLS = ["Read", "Grep", "Glob", "Bash", "Write", "Edit", "WebFetch", "WebSearch"]

# SDK SystemMessage 里 thinking_tokens 是逐 token 用量心跳，不是阶段变更；
# mcp_server_error / stream_error 携带工具注入失败信息，必须透传供排障
# （2026-08-19 audit 教训：MCP 工具被网关丢弃时唯一的线索在这类消息里）
_KEEP_SYSTEM_SUBTYPES = frozenset({"init", "mcp_server_error", "stream_error"})
_SUBAGENT_SUBTYPES = frozenset(
    {"task_started", "task_progress", "task_notification", "task_updated"}
)

# meta/事件截尾常量（集中管理，禁止散落魔数）
EVENT_TEXT_TRUNCATE = 2_000
ERROR_TEXT_TRUNCATE = 500
RAW_MESSAGE_TRUNCATE = 500
TRACEBACK_TRUNCATE = 1_000
SYSTEM_DETAIL_TRUNCATE = 400


# ── 通用小工具 ──


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


def usage_jsonable(value: Any) -> Any:
    """把 SDK usage / model_usage 收成可 json.dumps 的 dict，禁止 default=str。

    ResultMessage.usage 通常已是 dict；model_usage 值为 ModelUsage TypedDict
    （运行时也是 dict）。兼容网关/未来 SDK 若给出对象，必须抽出 token 字段，
    否则 sidecar 写成字符串后台账 isinstance(dict) 丢弃 → 计量全 0。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): usage_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [usage_jsonable(v) for v in value]
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
            return usage_jsonable(public)
    return value


def humanize_container_error(raw: str) -> tuple[str, str]:
    """容器内失败 → (标题, 下一步)。由 errors.classify_error 驱动。"""
    info = classify_error(raw)
    return info.title, info.hint


def failed_event(raw: str, **extra: Any) -> dict[str, Any]:
    """标准化结构化错误事件（code/category/title/hint/retryable/details）。"""
    info = classify_error(raw, extra_details=extra.get("details"))
    payload = {
        "type": "agent.failed",
        "code": str(info.code.value if hasattr(info.code, "value") else info.code),
        "category": str(
            info.category.value if hasattr(info.category, "value") else info.category
        ),
        "title": info.title,
        "hint": info.hint,
        "message": info.message,
        "error": _truncate(raw, ERROR_TEXT_TRUNCATE),
        "retryable": info.retryable,
        "details": info.details,
    }
    for k, v in extra.items():
        if k not in payload:
            payload[k] = v
    return payload


# ── 工作区发现（通用容器约定，非业务） ──


def discover_workspace_repo(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    skip = {".secrets", ".git"}
    found = [
        p
        for p in sorted(root.iterdir())
        if p.is_dir() and not p.name.startswith(".") and p.name not in skip
    ]
    return found[0] if found else None


def container_source_dir(spec: AgentSpec) -> str:
    """容器内源码根：优先 spec.source_path；否则扫 workspace_root 下真实仓库名。"""
    root = Path(spec.workspace_root)
    raw = str(spec.source_path or "").strip().replace("\\", "/")
    prefix = spec.workspace_root.rstrip("/") + "/"
    name = ""
    if raw.startswith(prefix):
        name = raw[len(prefix):].strip("/")
    elif raw:
        name = raw.rstrip("/").split("/")[-1]

    candidate = (root / name) if name else None
    if candidate is not None and candidate.is_dir():
        return f"{spec.workspace_root.rstrip('/')}/{name}"

    discovered = discover_workspace_repo(root)
    if discovered:
        return f"{spec.workspace_root.rstrip('/')}/{discovered.name}"
    if name:
        return f"{spec.workspace_root.rstrip('/')}/{name}"
    return spec.workspace_root


def sdk_cwd(spec: AgentSpec) -> str:
    """SDK cwd 必须是已存在的目录，否则 subprocess 直接炸 Working directory does not exist。"""
    root = Path(spec.workspace_root)
    mapped = container_source_dir(spec)
    prefix = spec.workspace_root.rstrip("/") + "/"
    rel = mapped[len(prefix):].strip("/") if mapped.startswith(prefix) else ""
    if rel:
        if (root / rel).is_dir():
            return f"{spec.workspace_root.rstrip('/')}/{rel}"
    discovered = discover_workspace_repo(root)
    if discovered:
        return f"{spec.workspace_root.rstrip('/')}/{discovered.name}"
    if root.is_dir():
        return spec.workspace_root
    return mapped


# ── skill（system prompt 追加正文）与 prompt ──


def load_skill(spec: AgentSpec) -> str | None:
    """加载 system_prompt 追加正文：skill_inline 优先，其次 skill_path 文件。"""
    if spec.skill_inline:
        return spec.skill_inline
    if spec.skill_path:
        path = Path(spec.skill_path)
        if not path.is_file():
            raise FileNotFoundError(f"skill 文件不存在: {spec.skill_path}")
        return path.read_text(encoding="utf-8")
    return None


def build_task_prompt(spec: AgentSpec) -> str:
    """user message：只带本轮输入 JSON；角色/工作流在 skill（system_prompt）。"""
    payload = json.dumps(spec.node_payload, ensure_ascii=False, indent=2, default=str)
    if spec.submit_schema:
        return f"按 system 完成任务。完成后必须调用 submit_result。\n\n输入(JSON):\n{payload}"
    return f"按 system 指令完成本次任务。\n\n输入(JSON):\n{payload}"


# ── SDK options 组装 ──


def build_options(
    spec: AgentSpec,
    *,
    model: str,
    cwd: str,
    hooks: dict[str, list[Any]],
    mcp_servers: dict[str, Any] | None = None,
) -> "ClaudeAgentOptions":
    """构造 ClaudeAgentOptions。文件/网络/进程边界由外层一次性容器负责。"""
    common: dict[str, Any] = {
        "model": model,
        "max_turns": spec.max_turns,
        "cwd": cwd,
        "tools": {"type": "preset", "preset": "claude_code"},
        "permission_mode": "bypassPermissions",
        "allowed_tools": list(BASE_ALLOWED_TOOLS) + list(spec.allowed_tools_extra),
        # 待审计仓库只作为数据读取，不加载其 CLAUDE.md/.claude 配置。
        "setting_sources": [],
        # 只使用平台显式传入的 MCP，忽略项目/用户/插件 MCP。
        "strict_mcp_config": True,
        # sandbox/bwrap 关闭：外层已是一次性容器（SCRUB=1/bwrap 与锁定 Docker 冲突）。
        "sandbox": {"enabled": False},
        "hooks": hooks,
    }
    if mcp_servers:
        common["allowed_tools"] = common["allowed_tools"] + [
            "mcp__crucible__submit_result",
            "mcp__crucible__read_slice",
        ]
    skill_text = load_skill(spec)
    if skill_text is not None:
        common["system_prompt"] = {
            "type": "preset",
            "preset": "claude_code",
            "append": skill_text,
        }
    effort = (os.environ.get("CLAUDE_CODE_EFFORT_LEVEL") or "").strip()
    if effort and effort != "auto":
        common["effort"] = effort
    if mcp_servers:
        common["mcp_servers"] = mcp_servers
    # 关键参数不再静默降级；构造失败由 run_spec 顶层统一输出 agent.failed
    return ClaudeAgentOptions(**common)


def build_mcp_servers(spec: AgentSpec, *, workspace_root: str, output_path: str) -> dict[str, Any] | None:
    """submit_schema 提供时注入 crucible MCP（submit_result + read_slice）。"""
    if not spec.submit_schema:
        return None
    from claude_agent_sdk import create_sdk_mcp_server

    server = create_sdk_mcp_server(
        name="crucible",
        tools=[
            make_submit_result_tool(spec.submit_schema, output_path=output_path),
            make_read_slice_tool(workspace_root=workspace_root),
        ],
    )
    return {"crucible": server}


# ── SDK Message → 扁平事件翻译（纯函数，不含 sequence/timestamp） ──


def _message_base(message: Any, session_seen: str | None) -> tuple[str | None, str | None, str | None]:
    sid = getattr(message, "session_id", None) or session_seen
    parent = getattr(message, "parent_tool_use_id", None) or None
    return sid, parent, type(message).__name__


def translate_system(
    message: Any, *, sid: str | None, parent: str | None
) -> list[dict[str, Any]]:
    """SystemMessage：Task 子代理生命周期 + 白名单 subtype 阶段事件。"""
    subtype = getattr(message, "subtype", None) or ""
    if subtype in _SUBAGENT_SUBTYPES:
        data = getattr(message, "data", {}) or {}
        return [
            {
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
                    json.dumps(data, ensure_ascii=False, default=str),
                    SYSTEM_DETAIL_TRUNCATE,
                ),
                "session_id": sid,
                "parent_tool_use_id": parent,
            }
        ]
    if subtype not in _KEEP_SYSTEM_SUBTYPES:
        return []
    if subtype == "init":
        phase, text = "start", subtype
    else:
        phase = "warning"
        text = (
            f"{subtype}: "
            f"{json.dumps(getattr(message, 'data', {}), ensure_ascii=False, default=str)[:SYSTEM_DETAIL_TRUNCATE]}"
        )
    return [
        {
            "type": "phase.updated",
            "phase": phase,
            "message": text,
            "session_id": sid,
            "parent_tool_use_id": parent,
        }
    ]


def translate_assistant(
    message: Any,
    *,
    sid: str | None,
    parent: str | None,
    tool_meta_by_id: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """AssistantMessage：错误 / thinking / 文本 / 工具调用。"""
    err = getattr(message, "error", None)
    if err:
        err_msg = getattr(err, "message", str(err))
        return [failed_event(err_msg, session_id=sid, parent_tool_use_id=parent)]

    content = getattr(message, "content", None) or []
    if not isinstance(content, list):
        content = [content]

    events: list[dict[str, Any]] = []
    for block in content:
        thinking_text = extract_thinking_text(block)
        if thinking_text:
            events.append(
                {
                    "type": "agent.thinking",
                    "text": thinking_text,
                    "model": getattr(message, "model", None),
                    "session_id": sid,
                    "parent_tool_use_id": parent,
                }
            )
            continue
        if isinstance(block, TextBlock):
            events.append(
                {
                    "type": "agent.message",
                    "text": getattr(block, "text", "") or "",
                    "model": getattr(message, "model", None),
                    "session_id": sid,
                    "parent_tool_use_id": parent,
                }
            )
        elif isinstance(block, ToolUseBlock):
            tool_name = getattr(block, "name", "unknown")
            tu_id = getattr(block, "id", None)
            meta_entry: dict[str, str] = {"tool": tool_name}
            if tool_name == "Bash":
                cmd = (getattr(block, "input", {}) or {}).get("command")
                if cmd:
                    meta_entry["command"] = str(cmd)
            if tu_id:
                # 结果块只有 id 没有名字，用 started 侧登记回填
                tool_meta_by_id[tu_id] = meta_entry
            events.append(
                {
                    "type": "tool.call.started",
                    "tool": tool_name,
                    "input": getattr(block, "input", {}) or {},
                    "tool_use_id": tu_id,
                    "session_id": sid,
                    "parent_tool_use_id": parent,
                }
            )
        else:
            # 未知 block：仍尝试当文本露出，避免静默丢流
            fallback = getattr(block, "text", None)
            if fallback:
                events.append(
                    {
                        "type": "agent.message",
                        "text": str(fallback),
                        "model": getattr(message, "model", None),
                        "session_id": sid,
                        "parent_tool_use_id": parent,
                    }
                )
    return events


def translate_user(
    message: Any,
    *,
    sid: str | None,
    parent: str | None,
    tool_meta_by_id: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """UserMessage（ToolResultBlock）：工具结果；started 侧回填 tool/command。"""
    content = getattr(message, "content", None) or []
    if not isinstance(content, list):
        content = [content]
    events: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, ToolResultBlock):
            continue
        # content 形态：str | list[dict]（SDK 0.2.x 原样透传 CLI 的块列表）
        # | list[对象]（旧形态兼容）。只提取文本，跳过 image 等块。
        raw_content = getattr(block, "content", "") or ""
        if isinstance(raw_content, list):
            parts: list[str] = []
            for b in raw_content:
                t = b.get("text") if isinstance(b, dict) else getattr(b, "text", None)
                if isinstance(t, str) and t:
                    parts.append(t)
            raw_content = " ".join(parts)
        result_id = getattr(block, "tool_use_id", None)
        meta = tool_meta_by_id.get(result_id) or {}
        event: dict[str, Any] = {
            "type": "tool.call.completed",
            "tool_use_id": result_id,
            "output": _truncate(raw_content, EVENT_TEXT_TRUNCATE),
            "is_error": bool(getattr(block, "is_error", False)),
            "session_id": sid,
            "parent_tool_use_id": parent,
        }
        if meta.get("tool"):
            event["tool"] = meta["tool"]
        if meta.get("command"):
            event["command"] = meta["command"]
        events.append(event)
    return events


def translate_result(message: Any, *, sid: str | None, parent: str | None) -> list[dict[str, Any]]:
    """ResultMessage（终态）：成功带全量 usage；失败转 agent.failed。"""
    result_text = getattr(message, "result", "") or ""
    if bool(getattr(message, "is_error", False)):
        return [
            failed_event(
                result_text or "SDK ResultMessage.is_error=true",
                session_id=sid,
                parent_tool_use_id=parent,
            )
        ]
    # usage = 主环；model_usage = 整树（官方 prefer）。透传有则记，禁止自算。
    return [
        {
            "type": "agent.completed",
            "reasoning": result_text,
            "session_id": sid,
            "duration_ms": getattr(message, "duration_ms", None),
            "total_cost_usd": getattr(message, "total_cost_usd", None),
            "num_turns": getattr(message, "num_turns", None),
            "usage": usage_jsonable(getattr(message, "usage", None)),
            "model_usage": usage_jsonable(getattr(message, "model_usage", None)),
            "is_error": False,
            "parent_tool_use_id": parent,
        }
    ]


def translate_message(
    message: Any,
    *,
    session_seen: str | None,
    tool_meta_by_id: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], str | None]:
    """分发翻译；返回 (事件列表, 新 session_id)。未知类型兜底 raw.message。"""
    sid, parent, _ = _message_base(message, session_seen)
    if isinstance(message, SystemMessage):
        return translate_system(message, sid=sid, parent=parent), sid
    if isinstance(message, AssistantMessage):
        return (
            translate_assistant(message, sid=sid, parent=parent, tool_meta_by_id=tool_meta_by_id),
            sid,
        )
    if isinstance(message, UserMessage):
        return (
            translate_user(message, sid=sid, parent=parent, tool_meta_by_id=tool_meta_by_id),
            sid,
        )
    if isinstance(message, ResultMessage):
        return translate_result(message, sid=sid, parent=parent), sid
    return (
        [
            {
                "type": "raw.message",
                "message_type": type(message).__name__,
                "raw": _truncate(str(message), RAW_MESSAGE_TRUNCATE),
                "session_id": sid,
                "parent_tool_use_id": parent,
            }
        ],
        sid,
    )


# ── 执行入口 ──


async def run_spec(
    spec: AgentSpec | dict[str, Any],
    cancel_event: asyncio.Event | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """执行一个 AgentSpec，产出扁平事件流（含 runner.exit 终帧）。

    - 逐事件写 transcript（NODE_TRANSCRIPT_PATH / spec.transcript_path）。
    - hook 审计事件与消息事件同队合流，sequence 单调递增。
    - cancel_event 置位时在下一事件边界发 agent.failed(取消) 并收尾。
    - 收尾写 .node_meta.json sidecar（usage/turns/cost/assistant 文本）。
    """
    if isinstance(spec, dict):
        spec = AgentSpec(**spec)

    output_path = env_output_path(spec.output_path)
    transcript = TranscriptWriter(env_transcript_path(spec.transcript_path))
    submit_enforced = spec.submit_schema is not None

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    seq = 0
    state: dict[str, Any] = {
        "assistant_texts": [],
        "completed": None,
        "saw_failure": False,
        "saw_llm_failure": False,
        "finalized": False,
        "cancelled": False,
        "infra_failure": False,
    }
    meta_holder: dict[str, Any] = {}

    def _emit(event: dict[str, Any]) -> None:
        nonlocal seq
        seq += 1
        event["sequence"] = event.get("sequence") or seq
        event.setdefault("timestamp", time.time())
        transcript.append(event)
        if event.get("type") == "agent.failed":
            state["saw_failure"] = True
            if classify_error(str(event.get("error") or "")).category == ErrorCategory.LLM_PROVIDER:
                state["saw_llm_failure"] = True
        if event.get("type") == "agent.message" and event.get("text"):
            state["assistant_texts"].append(str(event["text"]))
        if event.get("type") == "agent.completed":
            state["completed"] = event
        queue.put_nowait(event)

    # hook 审计事件（tool.call.denied / scrubbed / submit_nudge）走同一出口
    sink = _emit

    def _finalize() -> None:
        """退出码策略 + sidecar + 终帧（幂等：一个会话只收尾一次）。"""
        if state["finalized"]:
            return
        state["finalized"] = True
        completed = state["completed"]
        submitted = Path(output_path).is_file()
        meta = meta_holder.get("meta") or build_run_meta(
            node_key=spec.node_key, model="", prompt="", system_append=None
        )
        if state["infra_failure"] and completed is None:
            # 基础设施失败（SDK 缺失 / Provider 未注入 / 环境不完整）→ exit 2
            _emit_runner_exit(2, submitted=submitted, completed=False)
        elif submit_enforced and not submitted and not state["saw_llm_failure"] and not state["cancelled"]:
            _emit(
                failed_event(
                    f"任务 {spec.node_key} 未调用 submit_result（无提交产物）",
                )
            )
            _emit_runner_exit(1, submitted=False, completed=completed is not None)
        elif state["saw_failure"] and completed is None:
            _emit_runner_exit(1, submitted=submitted, completed=False)
        elif completed is None:
            # 容器被外力 kill 或 SDK 提前退出
            _emit_runner_exit(2, submitted=submitted, completed=False)
        else:
            _emit_runner_exit(0, submitted=submitted, completed=True)
        finalize_run_meta(
            meta, assistant_texts=state["assistant_texts"], completed_event=completed
        )
        write_meta_sidecar(meta, spec.meta_path)

    def _emit_runner_exit(
        exit_code: int, *, submitted: bool, completed: bool | None = None
    ) -> None:
        _emit(
            {
                "type": RUNNER_EXIT_EVENT,
                "exit_code": int(exit_code),
                "node_key": spec.node_key,
                "submitted": bool(submitted),
                "completed": bool(completed),
            }
        )
        queue.put_nowait(None)

    async def _consume() -> None:
        """消费 SDK query 流；结束（含异常/取消）时统一收尾。"""
        model = (os.environ.get("ANTHROPIC_MODEL") or "").strip()
        if SDK_IMPORT_ERROR:
            state["infra_failure"] = True
            _emit(failed_event(SDK_IMPORT_ERROR))
            _finalize()
            return
        if not model:
            state["infra_failure"] = True
            _emit(
                failed_event(
                    "ANTHROPIC_MODEL 未注入：Provider 配置缺失，无法执行（网关不内置默认模型）",
                    code="SPEC_INVALID",
                )
            )
            _finalize()
            return
        try:
            cwd = sdk_cwd(spec)
            hooks = build_policy_hooks(
                sink,
                workspace_root=spec.workspace_root,
                output_path=output_path,
                submit_enforced=submit_enforced,
            )
            mcp_servers = build_mcp_servers(
                spec, workspace_root=spec.workspace_root, output_path=output_path
            )
            options = build_options(spec, model=model, cwd=cwd, hooks=hooks, mcp_servers=mcp_servers)
            prompt = build_task_prompt(spec)
            try:
                meta_holder["meta"] = build_run_meta(
                    node_key=spec.node_key,
                    model=model,
                    prompt=prompt,
                    system_append=load_skill(spec),
                )
            except (OSError, FileNotFoundError):
                meta_holder["meta"] = build_run_meta(
                    node_key=spec.node_key, model=model, prompt=prompt, system_append=None
                )
        except Exception as e:
            state["infra_failure"] = True
            _emit(
                failed_event(
                    str(e),
                    exception=type(e).__name__,
                    traceback=_truncate(traceback.format_exc(), TRACEBACK_TRUNCATE),
                )
            )
            _finalize()
            return

        session_seen: str | None = None
        tool_meta_by_id: dict[str, dict[str, str]] = {}
        try:
            async for message in query(prompt=prompt, options=options):
                if getattr(message, "session_id", None):
                    session_seen = message.session_id
                events, _ = translate_message(
                    message,
                    session_seen=session_seen,
                    tool_meta_by_id=tool_meta_by_id,
                )
                for ev in events:
                    _emit(ev)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _emit(
                failed_event(
                    str(e),
                    exception=type(e).__name__,
                    traceback=_truncate(traceback.format_exc(), TRACEBACK_TRUNCATE),
                )
            )
        finally:
            _finalize()

    task = asyncio.create_task(_consume())
    cancelled = False
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                cancel_ev = {
                    "type": "agent.failed",
                    "error": "任务被外部取消",
                    "phase": "cancelled",
                }
                _emit(cancel_ev)
                yield cancel_ev
                break
    finally:
        if cancelled:
            state["cancelled"] = True
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
