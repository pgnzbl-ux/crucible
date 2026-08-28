"""agent-runner 容器 CLI 调试入口（生产主入口是 runner.server HTTP/SSE 守护）。

供 ``docker run -it`` 人工排障：读 /workspace/.node.json 构造 AgentSpec，
走与 HTTP 模式完全相同的 runner.gateway 执行，事件以 JSONL 逐行打到 stdout，
退出码取 runner.exit 终帧。

CLI 模式下的契约下发方式（与 HTTP 模式的 AgentSpec 字段对应）：
- submit schema：环境变量 NODE_SCHEMA_FILE 指向 JSON 文件（可选）；
- skill：默认探测 /node-skill/SKILL.md（worker 卷挂载）。

退出码：取 runner.exit（0 完成 / 1 失败 / 2 基础设施）；流异常中断默认 2。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from runner.schemas import RUNNER_EXIT_EVENT, AgentSpec

_DEFAULT_SKILL_PATH = "/node-skill/SKILL.md"


def load_debug_spec() -> AgentSpec:
    node_path = Path(os.environ.get("NODE_INPUT_PATH", "/workspace/.node.json"))
    raw: dict[str, Any] = json.loads(node_path.read_text(encoding="utf-8"))
    submit_schema = None
    schema_file = os.environ.get("NODE_SCHEMA_FILE")
    if schema_file:
        submit_schema = json.loads(Path(schema_file).read_text(encoding="utf-8"))
    skill_path = _DEFAULT_SKILL_PATH if Path(_DEFAULT_SKILL_PATH).is_file() else None
    return AgentSpec(
        node_key=str(raw.get("node_key") or os.environ.get("NODE_KEY") or "debug"),
        node_payload=dict(raw.get("input_json") or {}),
        submit_schema=submit_schema,
        skill_path=skill_path,
    )


async def _run() -> int:
    from runner.gateway import run_spec

    try:
        spec = load_debug_spec()
    except Exception as e:  # noqa: BLE001 — 输入损坏要给出结构化报错
        print(
            json.dumps(
                {"type": "agent.failed", "error": f"节点输入读取失败: {e}"},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 2
    exit_code = 2  # 流中断/无终帧按基础设施错误处理
    async for event in run_spec(spec):
        print(json.dumps(event, ensure_ascii=False, default=str), flush=True)
        if event.get("type") == RUNNER_EXIT_EVENT:
            exit_code = int(event.get("exit_code") or 0)
    return exit_code


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
