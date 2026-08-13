"""
Runner Bridge — 拉起 agent-runner 容器 + 同步流式消费的薄封装。

将 core/agent_runner.py 的同步接口与 agent context 的语义需求粘合：
- 写 .prompt.json 到 host_workdir
- 构造 AgentRunnerSpec
- 调 agent_runner_manager.run_with_streaming(spec, on_event)

为什么单独拆出 runner_bridge：
- sdk_adapter.py 负责"注入什么（env + prompt）"
- agent_runner.py 负责"容器编排（spec + docker SDK）"
- runner_bridge.py 负责"组合两者 + 写盘 prompt"

将 ClaudeSdkExecutor 与底层解耦，让 executor.py 只关心业务语义（结论 / 事件流）。
"""

# ⚠️ DEPRECATED: 已被 6 节点编排(orchestrator + ai_runner)取代,保留仅作历史参考。


from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from app.core.agent_runner import AgentRunnerSpec, agent_runner_manager, AgentRunnerError

from .sdk_adapter import ClaudeSdkAdapter

logger = logging.getLogger(__name__)


def prepare_runner_spec(
    ctx: dict[str, Any],
    host_workdir: str,
    runner_env: dict[str, str],
) -> AgentRunnerSpec:
    """组合 ClaudeSdkAdapter 与 AgentRunner 之间的输入：env 已构造好，spec 直接组装。"""
    adapter = ClaudeSdkAdapter()
    return AgentRunnerSpec(
        env=runner_env,  # 由 tasks.py 通过 resolve_runner_env 注入
        host_workdir=host_workdir,
        extra_labels={
            "task_id": ctx.get("task_id", ""),
            "run_id": ctx.get("run_id", ""),
        },
    )


def write_prompt_json(host_workdir: str, ctx: dict[str, Any]) -> str:
    """把任务参数写到 host_workdir/.prompt.json，返回绝对路径。"""
    import json
    import os

    adapter = ClaudeSdkAdapter()
    payload = adapter.build_prompt_payload(ctx)
    path = os.path.join(host_workdir, ".prompt.json")
    os.makedirs(host_workdir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(payload)
    return path


def stream_runner(
    spec: AgentRunnerSpec,
    on_event: Callable[[dict], None],
) -> tuple[int, dict]:
    """同步拉起容器 + 流式消费。返回 (exit_code, summary)。

    失败抛 AgentRunnerError，调用方负责落库。
    """
    try:
        return agent_runner_manager.run_with_streaming(spec, on_event)
    except AgentRunnerError:
        raise
    except Exception as e:  # noqa: BLE001 — 兜底
        raise AgentRunnerError(f"agent-runner 编排异常: {e}") from e