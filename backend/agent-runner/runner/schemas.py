"""统一 Agent 容器交互规范模型 (Agent Container ABI Schemas).

Runner 是通用 Claude Agent SDK 执行网关：契约全部由 backend 下发 —
submit schema、skill 正文/路径、节点身份、策略参数都随 AgentSpec 传入，
runner 内不内置任何业务节点知识。

事件 wire 格式唯一为 AgentEventEnvelope（SSE `data:` 帧）；transcript.jsonl
与 backend on_event 回调继续消费解包后的扁平事件（type + 业务字段平铺）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentSpec(BaseModel):
    """一次 Agent 执行的完整入参（HTTP body / CLI 调试入口共用）。

    node_key 仅作标识与审计；gateway 不据其分支任何业务行为。
    submit_schema 提供时才注入 submit_result MCP 工具并启用产出门禁；
    skill_path / skill_inline 提供时才追加 system_prompt。
    """

    protocol_version: str = "3.0"
    task_id: str = ""
    run_id: str = ""
    node_key: str
    attempt: int = 1

    node_payload: dict[str, Any] = Field(default_factory=dict)

    # 提交契约（backend 单一来源下发；Anthropic 工具 schema 子集）
    submit_schema: dict[str, Any] | None = None
    # system prompt 追加正文：容器内路径（如 /node-skill/SKILL.md）或直接内联
    skill_path: str | None = None
    skill_inline: str | None = None

    # 执行策略
    max_turns: int = 480
    workspace_root: str = "/workspace"
    # cwd 提示（通常为 node_payload.source_path 的容器内路径）；
    # 缺省时 gateway 在 workspace_root 下自动发现仓库目录
    source_path: str | None = None
    # 额外放行的内置工具（如批量审议模式追加 "Task"）
    allowed_tools_extra: list[str] = Field(default_factory=list)

    # 产出文件路径覆盖；缺省读 NODE_OUTPUT_PATH/NODE_META_PATH/NODE_TRANSCRIPT_PATH env
    output_path: str | None = None
    meta_path: str | None = None
    transcript_path: str | None = None


class AgentEventEnvelope(BaseModel):
    """统一实时事件信封（SSE wire 唯一格式）。

    payload 收纳全部业务字段；解包 = {type, sequence, timestamp, session_id,
    parent_tool_use_id, **payload}，与 stdout JSONL 时代的扁平事件完全兼容。
    """

    event_type: str
    sequence: int
    timestamp: float
    session_id: str | None = None
    parent_tool_use_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# 终态帧：网关执行收尾（含退出码策略结果），驱动层据此合成 exit_code。
# 不进入 on_event 回调（backend 下游无需感知），但写入 transcript 供审计。
RUNNER_EXIT_EVENT = "runner.exit"


class RunnerExit(BaseModel):
    """gateway 执行收尾摘要（SSE 终帧 payload）。"""

    exit_code: int
    node_key: str = ""
    submitted: bool = False
    completed: bool = False


def encode_envelope(flat: dict[str, Any]) -> dict[str, Any]:
    """扁平事件 → AgentEventEnvelope dict（wire 格式）。"""
    envelope_keys = ("type", "sequence", "timestamp", "session_id", "parent_tool_use_id")
    payload = {k: v for k, v in flat.items() if k not in envelope_keys}
    return {
        "event_type": str(flat.get("type") or "raw.message"),
        "sequence": int(flat.get("sequence") or 0),
        "timestamp": float(flat.get("timestamp") or 0.0),
        "session_id": flat.get("session_id"),
        "parent_tool_use_id": flat.get("parent_tool_use_id"),
        "payload": payload,
    }


def decode_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    """AgentEventEnvelope dict → 扁平事件（backend on_event 消费格式）。"""
    event_type = envelope.get("event_type") or envelope.get("type") or "raw.message"
    flat = dict(envelope.get("payload") or {})
    flat["type"] = str(event_type)
    for key in ("sequence", "timestamp", "session_id", "parent_tool_use_id"):
        if key in envelope:
            flat[key] = envelope[key]
    return flat
