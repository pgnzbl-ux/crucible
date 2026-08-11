"""
Agent 执行器抽象。

设计：执行器 = 平台能力而非胶水代码。
- ClaudeSdkExecutor：通过 agent-runner 容器执行 Claude Agent SDK（生产路径）
- MockExecutor：开发模式模拟分析，验证全链路无真实凭据

执行器输入 AgentRunContext，输出 AgentResult（结构化结论 + 事件流）。

SDK 改造要点（v0.3.0+）：
- 适配 Claude Agent SDK（@anthropic-ai/claude-agent-sdk），不再 spawn `claude` CLI
- 凭据通过 docker run --env 注入 agent-runner 容器，零落盘
- 流式事件由 agent-runner 容器内 run_one.py 输出 JSONL，executor 同步消费
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from app.core.agent_runner import AgentRunnerError, agent_runner_manager
from app.core.config import get_settings

from .runner_bridge import prepare_runner_spec, write_prompt_json

settings = get_settings()


# ── 领域对象 ──


@dataclass
class AgentRunContext:
    """一次 Agent 执行的全部上下文"""

    task_id: str
    run_id: str
    project_address: str
    project_ref: str | None
    vulnerability_description: str
    host_workdir: str = ""                            # host 临时目录（worker bind mount 源）
    workdir_container: str = "/workspace/project"     # 容器内 workdir
    runner_env: dict[str, str] = field(default_factory=dict)  # 已构造好的容器 env
    secret_files: list[dict] = field(default_factory=list)    # 注入的凭据描述（告知 agent 可用 env/文件，P1-6）


@dataclass
class AgentResult:
    """Agent 执行结果"""

    conclusion: str = "unknown"            # exists | not_exists | unconfirmed | failed | cancelled
    reasoning: str = ""                    # 分析推理全文
    events: list[dict[str, Any]] = field(default_factory=list)  # 结构化事件流
    exit_code: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error_message: str | None = None
    session_id: str | None = None           # SDK 原生 session_id（透传，可用于多轮恢复）
    container_id: str | None = None         # agent-runner 容器 id（用于取消/调试）


class AgentExecutor(Protocol):
    """执行器协议"""

    def run(self, ctx: AgentRunContext, on_event: Callable[[dict], None] | None = None) -> AgentResult: ...


# ── 生产执行器：Claude Agent SDK（agent-runner 容器内） ──


class ClaudeSdkExecutor:
    """拉起 agent-runner 容器 → 流式消费 SDK 事件 → 汇总为 AgentResult。

    同步签名（与 MockExecutor 形态一致）：
      - Celery task 本体同步，通过 asyncio.to_thread(executor.run, ctx) 调用
      - on_event 由 tasks.py 提供，内部用 asyncio.run_coroutine_threadsafe 跨入主 loop 落库
    """

    def __init__(self, adapter=None) -> None:
        # adapter 参数保留向后兼容；当前实现只通过 ctx.runner_env 注入
        pass

    def run(
        self,
        ctx: AgentRunContext,
        on_event: Callable[[dict], None] | None = None,
    ) -> AgentResult:
        result = AgentResult()
        result.events.append(self._phase_event("start", "开始漏洞分析（Claude Agent SDK）"))

        if not ctx.host_workdir:
            result.exit_code = -1
            result.error_message = "AgentRunContext.host_workdir 必须设置"
            result.conclusion = "failed"
            result.finished_at = time.time()
            result.events.append(self._phase_event("failed", result.error_message))
            return result

        # 1. 构造 spec（env 已由 tasks.py 在外层解析好，含 env_var 凭据）
        ctx_dict = {
            "task_id": ctx.task_id,
            "run_id": ctx.run_id,
            "project_address": ctx.project_address,
            "project_ref": ctx.project_ref,
            "vulnerability_description": ctx.vulnerability_description,
            "secret_files": ctx.secret_files,
        }
        spec = prepare_runner_spec(ctx=ctx_dict, host_workdir=ctx.host_workdir, runner_env=ctx.runner_env)

        # 2. 写 .prompt.json（容器内 read，含 secret_files 告知 agent 可用凭据）
        try:
            write_prompt_json(ctx.host_workdir, ctx=ctx_dict)
        except OSError as e:
            result.exit_code = -1
            result.error_message = f"写 prompt 失败: {e}"
            result.conclusion = "failed"
            result.finished_at = time.time()
            result.events.append(self._phase_event("failed", result.error_message))
            return result

        # 3. 流式拉起 + 回调
        def _on_event(event: dict) -> None:
            result.events.append(event)
            if on_event:
                on_event(event)

        try:
            exit_code, summary = agent_runner_manager.run_with_streaming(
                spec=spec,
                on_event=_on_event,
            )
            result.exit_code = exit_code
            result.container_id = summary.get("container_id")
        except AgentRunnerError as e:
            result.exit_code = -1
            result.error_message = f"agent-runner 编排失败: {e}"
            result.conclusion = "failed"
            result.finished_at = time.time()
            result.events.append(self._phase_event("failed", result.error_message))
            return result

        # 4. 提取结论（从事件流的 agent.completed 反向解析）
        if exit_code == 137:
            result.conclusion = "cancelled"
            result.error_message = "agent_runner_killed"
        elif exit_code != 0 and exit_code != 2:
            # 2 = 基础设施错误（OCI / 镜像）；其它非 0 = 业务失败
            stderr_tail = ""
            try:
                if result.container_id:
                    stderr_tail = agent_runner_manager._client.containers.get(
                        result.container_id
                    ).logs(tail=50, stdout=False, stderr=True).decode("utf-8", errors="replace")
            except Exception:
                pass
            result.conclusion = "failed"
            result.error_message = (stderr_tail or result.error_message or "agent_runner 非 0 退出")[:500]
        else:
            # 0 或 2：从事件流取 agent.completed
            for ev in reversed(result.events):
                if ev.get("type") == "agent.completed":
                    result.reasoning = ev.get("reasoning", "") or ""
                    result.session_id = ev.get("session_id")
                    result.conclusion = ev.get("conclusion") or self._classify_conclusion(result.reasoning)
                    break
            else:
                # 没有 agent.completed（如 OOM 提前终止）
                if exit_code == 2:
                    result.conclusion = "failed"
                    result.error_message = "agent_runner_exit_2_no_completion"
                else:
                    result.conclusion = self._classify_conclusion(result.reasoning)

        result.finished_at = time.time()
        result.events.append(self._phase_event(
            "completed" if result.conclusion not in ("failed", "cancelled") else "failed",
            f"分析完成，结论: {result.conclusion}",
        ))
        return result

    @staticmethod
    def _classify_conclusion(text: str) -> str:
        """文本匹配：exists / not_exists / unconfirmed"""
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

    @staticmethod
    def _phase_event(phase: str, message: str) -> dict[str, Any]:
        return {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "type": "phase.updated",
            "phase": phase,
            "message": message,
            "timestamp": time.time(),
        }


# ─ ── 开发执行器：Mock（无真实凭据时验证链路） ─ ──


class MockExecutor:
    """开发模式：模拟 Agent 分析流程，产出可预期的结论。

    不依赖 SDK / 容器，纯粹返回预定义事件流，便于本地全链路冒烟。
    """

    def run(
        self,
        ctx: AgentRunContext,
        on_event: Callable[[dict], None] | None = None,
    ) -> AgentResult:
        result = AgentResult()
        result.session_id = f"mock-{uuid.uuid4().hex[:12]}"

        # 模拟阶段事件（保持与 ClaudeSdkExecutor 形态对齐）
        phases = [
            ("start", "开始漏洞分析（Mock）"),
            ("preflight", "克隆项目源码"),
            ("scanning", "白盒审计代码调用链"),
            ("reproducing", "尝试复现漏洞"),
            ("completed", "分析完成"),
        ]
        for i, (phase, msg) in enumerate(phases):
            ev = {
                "event_id": f"evt_{uuid.uuid4().hex[:12]}",
                "type": "phase.updated",
                "phase": phase,
                "message": msg,
                "sequence": i + 1,
                "timestamp": time.time(),
            }
            result.events.append(ev)
            if on_event:
                on_event(ev)

        # 模拟工具调用
        ev = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "type": "tool.call.completed",
            "tool": "read_file",
            "input": {"path": f"{ctx.workdir_container}/src/main.py"},
            "sequence": 5,
            "timestamp": time.time(),
        }
        result.events.append(ev)
        if on_event:
            on_event(ev)

        # 模拟 agent.completed（与 SDK 翻译格式一致）
        conclusion = "unconfirmed"
        reasoning = (
            f"[Mock 模式] 任务 {ctx.task_id[:8]} 的分析请求已受理。\n"
            f"项目: {ctx.project_address}@{ctx.project_ref or 'default'}\n"
            f"漏洞描述: {ctx.vulnerability_description[:200]}\n\n"
            "这是开发模式的模拟输出，未连接真实 Claude Agent SDK。"
            "设置 CLAUDE_AGENT_SDK_ENABLED=true 并配置 LLM_API_KEY 后启用真实分析。"
        )
        ev = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "type": "agent.completed",
            "conclusion": conclusion,
            "reasoning": reasoning,
            "session_id": result.session_id,
            "sequence": 6,
            "timestamp": time.time(),
        }
        result.events.append(ev)
        if on_event:
            on_event(ev)

        result.conclusion = conclusion
        result.reasoning = reasoning
        result.exit_code = 0
        result.finished_at = time.time()
        return result


# ─ ── 执行器工厂 ─ ──


def get_executor() -> AgentExecutor:
    """按配置返回执行器实例"""
    if settings.claude_agent_sdk_enabled:
        return ClaudeSdkExecutor()
    return MockExecutor()