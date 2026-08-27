"""Provider Agent 兼容性测试。

通过 agent-runner 验证 Read、Bash、MCP、多轮、结束态，以及 Bash 子进程
看不到模型主凭据。探针只记录布尔值与变量名，从不输出凭据值。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4

from app.contexts.agent.ai_runner import run_ai_node
from app.contexts.agent.errors import humanize_agent_error
from app.contexts.agent.sdk_adapter import ClaudeSdkAdapter
from app.core.agent_runner import AgentRunnerError, agent_runner_manager
from app.core.config import get_settings

from .provider_runtime import ProviderRuntimeConfig
from .schemas import LlmAgentCanaryChecks, LlmProviderAgentTestResult

logger = logging.getLogger(__name__)

CANARY_DEADLINE_SECONDS = 300
# 抗抖动：LLM/网关非确定性抖动会让一次性采样随机红。仅对"瞬时类"失败
# 自动重试一次；凭据泄露、工具被拒等确定性失败重试没有意义。
MAX_CANARY_ATTEMPTS = 2
_TRANSIENT_MARKERS = ("LLM 调用失败", "未调用 submit_result", "兼容测试超时")
_AUTH_ENV_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_TEXT_EXCERPT = 240


def _failure(
    runtime: ProviderRuntimeConfig,
    message: str,
    *,
    checks: LlmAgentCanaryChecks | None = None,
    started: float | None = None,
    meta: dict | None = None,
) -> LlmProviderAgentTestResult:
    meta = meta or {}
    return LlmProviderAgentTestResult(
        ok=False,
        message=message,
        checks=checks or LlmAgentCanaryChecks(),
        provider_id=runtime.id,
        model=runtime.model,
        duration_ms=(int((time.monotonic() - started) * 1000) if started else None),
        num_turns=_optional_int(meta.get("num_turns")),
        usage=_usage(meta.get("usage")),
    )


def _optional_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _usage(value) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, raw in value.items():
        try:
            result[str(key)] = int(raw)
        except (TypeError, ValueError):
            continue
    return result


def _excerpt(text: object, limit: int = _TEXT_EXCERPT) -> str:
    return " ".join(str(text or "").split())[:limit]


def _normalize_marker_text(value: object) -> str:
    """去掉 Claude Code Read 工具常见的「行号\\t内容」前缀后再比对。

    弱模型常把工具原始输出原样填进 marker，导致误判 read_tool。
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if "\t" in text:
        left, right = text.split("\t", 1)
        if left.strip().isdigit() and right:
            return right.strip()
    matched = re.match(r"^\s*\d+\s*[|:]\s*(.*)$", text, re.DOTALL)
    if matched and matched.group(1).strip():
        return matched.group(1).strip()
    return text


def _read_probe(workdir: Path) -> dict:
    try:
        return json.loads(
            (workdir / "canary" / "probe-result.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_sidecar_meta(workdir: Path, meta: dict) -> None:
    """output 缺失时 ai_runner 会提前抛错；从控制目录补读安全 sidecar。"""
    for path in sorted((workdir / ".runner").glob("*/node_meta.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            for key, item in value.items():
                meta.setdefault(key, item)
            return


def _observed_checks(
    *,
    events: list[tuple[str, str]],
    probe: dict,
    meta: dict,
    output: dict | None = None,
    marker: str | None = None,
) -> LlmAgentCanaryChecks:
    tool_names = [tool for event_type, tool in events if event_type == "tool.call.started"]
    terminals = [
        event_type
        for event_type, _tool in events
        if event_type in {"agent.completed", "agent.failed"}
    ]
    probe_ok = probe.get("python_ok") is True
    read_observed = "Read" in tool_names
    marker_ok = (
        output is None
        or marker is None
        or _normalize_marker_text(output.get("marker")) == marker
    )
    # 多轮以实证为准：num_turns 口径随网关漂移（有的恒报 1），
    # 但「读文件→提交结果」本身就是两次独立模型回合的铁证。
    submitted = any(name.endswith("__submit_result") for name in tool_names)
    multi_turn_observed = (
        (_optional_int(meta.get("num_turns")) or 0) >= 2
        or (read_observed and submitted)
    )
    # 凭据隔离以探针文件为准，不采信模型自报。
    return LlmAgentCanaryChecks(
        read_tool=read_observed and marker_ok,
        bash_tool="Bash" in tool_names
        and probe_ok
        and (output is None or output.get("probe_completed") is True),
        mcp_submit=output is not None
        and submitted,
        multi_turn=multi_turn_observed,
        credential_isolation=probe_ok and probe.get("credential_visible") is False,
        single_terminal=len(terminals) == 1,
    )


def _evidence_lines(events: list[tuple[str, str]], meta: dict) -> list[str]:
    """失败证据摘要：工具序列 + 模型末条回复片段，供 UI 直接归因。"""
    lines: list[str] = []
    tool_seq = [tool for event_type, tool in events if event_type == "tool.call.started"]
    if tool_seq:
        lines.append(f"工具序列：{' > '.join(tool_seq)}")
    terminals = [t for t, _ in events if t in {"agent.completed", "agent.failed"}]
    if terminals:
        lines.append(f"结束态：{' > '.join(terminals)}")
    assistant_excerpt = _excerpt(meta.get("assistant_text"), 160)
    if assistant_excerpt:
        lines.append(f"模型末条回复：{assistant_excerpt}")
    return lines


def _is_transient_failure(result_message: str) -> bool:
    return any(marker in result_message for marker in _TRANSIENT_MARKERS)


def _write_probe(workdir: Path, marker: str) -> None:
    canary_dir = workdir / "canary"
    canary_dir.mkdir(mode=0o777)
    canary_dir.chmod(0o777)
    (canary_dir / "marker.txt").write_text(marker, encoding="utf-8")
    probe_source = f'''from __future__ import annotations

import json
import os
from pathlib import Path

names = {list(_AUTH_ENV_NAMES)!r}
visible = [name for name in names if os.environ.get(name)]
result = {{
    "python_ok": True,
    "credential_visible": bool(visible),
    "visible_names": visible,
}}
Path("/workspace/canary/probe-result.json").write_text(
    json.dumps(result, ensure_ascii=False), encoding="utf-8"
)
print(json.dumps(result, ensure_ascii=False))
'''
    (canary_dir / "probe.py").write_text(probe_source, encoding="utf-8")


async def _stop_canary(task_id: str, workdir: Path) -> None:
    await asyncio.to_thread(
        agent_runner_manager.remove_for_task,
        task_id,
        str(workdir),
    )


async def _canary_once(provider) -> LlmProviderAgentTestResult:
    """单次尝试：使用指定 Provider 跑一次真实 Docker Agent 兼容性测试。"""
    runtime = (
        provider
        if isinstance(provider, ProviderRuntimeConfig)
        else ProviderRuntimeConfig.from_provider(provider)
    )
    settings = get_settings()
    if not settings.claude_agent_sdk_enabled:
        return _failure(runtime, "未启用 Claude Agent SDK，无法运行 Agent 兼容测试")
    if not runtime.credential.strip():
        return _failure(runtime, "Provider 未配置 API Key")
    if not await asyncio.to_thread(
        agent_runner_manager.image_exists, settings.agent_runner_image
    ):
        return _failure(runtime, f"agent-runner 镜像不存在: {settings.agent_runner_image}")

    canary_id = uuid4().hex[:12]
    task_id = f"provider-canary-{canary_id}"
    workdir = Path(agent_runner_manager.host_workdir_path(task_id))
    marker = f"crucible-canary-{uuid4().hex}"
    runner_env = ClaudeSdkAdapter().build_runner_env(runtime.agent_env())
    events: list[tuple[str, str]] = []
    meta: dict = {}
    started = time.monotonic()
    run_task: asyncio.Task | None = None

    def on_event(event: dict) -> None:
        event_type = str(event.get("type") or "")
        tool = str(event.get("tool") or "")
        events.append((event_type, tool))

    try:
        workdir.mkdir(parents=True, mode=0o777, exist_ok=False)
        workdir.chmod(0o777)
        _write_probe(workdir, marker)
        run_task = asyncio.create_task(
            run_ai_node(
                node_key="canary",
                input_json={
                    "marker_path": "/workspace/canary/marker.txt",
                    "probe_command": "python /workspace/canary/probe.py",
                },
                host_workdir=str(workdir),
                runner_env=runner_env,
                on_event=on_event,
                task_id=task_id,
                meta_out=meta,
            )
        )
        try:
            output = await asyncio.wait_for(
                asyncio.shield(run_task), timeout=CANARY_DEADLINE_SECONDS
            )
        except TimeoutError:
            await _stop_canary(task_id, workdir)
            try:
                await asyncio.wait_for(run_task, timeout=20)
            except (TimeoutError, asyncio.CancelledError, AgentRunnerError):
                run_task.cancel()
            timeout_result = _failure(
                runtime,
                f"Agent 兼容测试超时（{CANARY_DEADLINE_SECONDS} 秒），已回收容器；慢网关请调大 Provider timeout_ms 后重试",
                started=started,
                meta=meta,
            )
            timeout_result.evidence = _evidence_lines(events, meta)
            return timeout_result

        probe = _read_probe(workdir)
        terminals = [
            event_type
            for event_type, _tool in events
            if event_type in {"agent.completed", "agent.failed"}
        ]
        num_turns = _optional_int(meta.get("num_turns"))
        checks = _observed_checks(
            events=events,
            probe=probe,
            meta=meta,
            output=output,
            marker=marker,
        )
        failed = [name for name, passed in checks.model_dump().items() if not passed]
        ok = not failed and terminals == ["agent.completed"]
        evidence_lines = _evidence_lines(events, meta)
        if ok:
            message = "Agent 兼容测试通过"
        else:
            message = "Agent 兼容测试未通过: " + ", ".join(failed)
            probe_ok = probe.get("python_ok") is True
            if "bash_tool" in failed or "credential_isolation" in failed:
                if not probe_ok:
                    message += "（Bash 未成功执行探测命令）"
                elif probe.get("credential_visible") is True:
                    names = ",".join(str(n) for n in (probe.get("visible_names") or []))
                    message += f"（Bash 子进程仍可见: {names or '凭据变量'}）"

        return LlmProviderAgentTestResult(
            ok=ok,
            message=message,
            checks=checks,
            provider_id=runtime.id,
            model=runtime.model,
            duration_ms=int((time.monotonic() - started) * 1000),
            num_turns=num_turns,
            usage=_usage(meta.get("usage")),
            evidence=evidence_lines,
        )
    except AgentRunnerError as exc:
        _merge_sidecar_meta(workdir, meta)
        probe = _read_probe(workdir)
        checks = _observed_checks(
            events=events,
            probe=probe,
            meta=meta,
        )
        observed_tools = sorted(
            {tool for event_type, tool in events if event_type == "tool.call.started"}
        )
        observed = "、".join(observed_tools) if observed_tools else "无"
        raw_error = str(exc)
        assistant_excerpt = _excerpt(meta.get("assistant_text"))
        excerpt_suffix = (
            f" 模型末条回复：{assistant_excerpt}" if assistant_excerpt else ""
        )
        if "未调用 submit_result" in raw_error:
            if any(name.endswith("__submit_result") for name in observed_tools):
                detail = "已发起结果提交，但未成功落盘。"
            elif observed_tools:
                detail = "已调用基础工具，但未发起结果提交；工具路由可能不兼容。"
            else:
                detail = "未发起任何工具调用。"
            message = (
                f"Agent 未提交结构化结果：{detail} 已观察工具：{observed}"
                f"{excerpt_suffix}"
            )
        else:
            title, hint = humanize_agent_error(raw_error)
            message = f"{title}：{hint} 已观察工具：{observed}{excerpt_suffix}"
        logger.warning(
            "agent compatibility test failed provider=%s: %s",
            runtime.id,
            _excerpt(raw_error, 200),
        )
        failed_result = _failure(
            runtime,
            message,
            checks=checks,
            started=started,
            meta=meta,
        )
        failed_result.evidence = _evidence_lines(events, meta)
        return failed_result
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "agent compatibility test infrastructure failure id=%s", canary_id
        )
        return _failure(
            runtime,
            f"Agent 兼容测试异常: {type(exc).__name__}",
            started=started,
            meta=meta,
        )
    finally:
        await _stop_canary(task_id, workdir)
        shutil.rmtree(workdir, ignore_errors=True)


async def run_provider_agent_canary(provider) -> LlmProviderAgentTestResult:
    """对非确定性 LLM/网关做抗抖动采样：瞬时类失败自动再试一次。

    确定性失败（凭据泄露、工具被拒、镜像缺失等）不重试——重跑不会改变结论。
    evidence 按次序累积，前端可逐条展开归因。
    """
    aggregate_evidence: list[str] = []
    for attempt in range(1, MAX_CANARY_ATTEMPTS + 1):
        result = await _canary_once(provider)
        prefix = f"[第 {attempt} 次] "
        lines = result.evidence or []
        aggregate_evidence.extend(prefix + line for line in lines) if lines else \
            aggregate_evidence.append(f"{prefix}（无工具事件）")
        result.attempts = attempt
        if result.ok or attempt >= MAX_CANARY_ATTEMPTS or not _is_transient_failure(result.message):
            result.evidence = aggregate_evidence
            if not result.ok and attempt > 1:
                result.message = f"自动重试后仍未通过。{result.message}"
            elif result.ok and attempt > 1:
                result.message = f"第 {attempt} 次尝试通过（前次为瞬时抖动）。{result.message}"
            return result
