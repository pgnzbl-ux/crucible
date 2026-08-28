"""gateway 注入的 SDK MCP 工具（mcp__crucible__ 命名空间）。

- submit_result：按 backend 下发的 submit_schema 接收结构化结果并落盘。
  schema 为 None 时本工具不注入。SDK 0.2.134 确认 create_sdk_mcp_server +
  @tool 原生支持自定义工具注入；形状的强校验在 backend validate_output。
- read_slice：压缩产物（单行超长文件）的有界读取，是 Read/Grep 被
  read guard deny 后的官方出路。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable

# read_slice 输出边界：单次硬上限 8KB / 最多 20 处命中
READ_SLICE_MAX_OUTPUT = 8_192
READ_SLICE_MAX_MATCHES = 20

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


def _resolve_output_path(override: str | None) -> Path:
    return Path(override or os.environ.get("NODE_OUTPUT_PATH", "/workspace/.node_output.json"))


def make_submit_result_tool(schema: dict[str, Any], *, output_path: str | None = None) -> Callable:
    """构造 submit_result MCP 工具：agent 调用时把 input 写到产出文件。"""
    from claude_agent_sdk import tool

    @tool(
        name="submit_result",
        description="提交本任务的结构化结果。完成后必须调用此工具。",
        input_schema=schema,
    )
    async def submit_result(input: dict) -> dict:
        out_path = _resolve_output_path(output_path)
        out_path.write_text(
            json.dumps(input, ensure_ascii=False, default=str), encoding="utf-8"
        )
        return {"status": "submitted", "fields": list(input.keys())}

    return submit_result


def read_slice_impl(
    file_path: str,
    pattern: str | None = None,
    byte_offset: int = 0,
    byte_length: int = 4096,
    context: int = 300,
    *,
    root: str = "/workspace",
) -> dict:
    """read_slice 的纯函数实现（容器外可单测）。

    pattern 模式：全文按字节正则扫描，返回命中点 ±context 片段（附 byte_offset
    供窗口模式翻页）；窗口模式：返回 [byte_offset, byte_offset+byte_length)。
    两种模式输出都受 READ_SLICE_MAX_OUTPUT 硬上限约束。
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
        budget = READ_SLICE_MAX_OUTPUT
        capped = False
        for m in rx.finditer(data):
            if len(matches) >= READ_SLICE_MAX_MATCHES or budget <= 0:
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
    byte_length = min(max(int(byte_length), 1), READ_SLICE_MAX_OUTPUT)
    chunk = data[byte_offset: byte_offset + byte_length]
    return {
        "file": str(target),
        "size_bytes": size,
        "byte_offset": byte_offset,
        "excerpt": chunk.decode("utf-8", errors="replace"),
        "has_more": byte_offset + byte_length < size,
    }


def make_read_slice_tool(*, workspace_root: str = "/workspace") -> Callable:
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
        return read_slice_impl(
            str(input.get("file_path") or ""),
            pattern=input.get("pattern"),
            byte_offset=0 if byte_offset is None else int(byte_offset),
            byte_length=4096 if byte_length is None else int(byte_length),
            context=300 if context is None else int(context),
            root=workspace_root,
        )

    return read_slice
