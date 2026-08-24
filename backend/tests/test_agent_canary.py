"""Provider Agent canary 必须以真实事件/探针判定，不能相信模型自报。"""

import json
from types import SimpleNamespace

import pytest

from app.contexts.settings import agent_canary


def _provider():
    return SimpleNamespace(
        id="provider-1",
        provider_type="custom",
        auth_mode="bearer",
        base_url="https://gateway.example",
        api_key_encrypted="secret",
        model="model-1",
        timeout_ms=60_000,
        temperature=0.2,
        max_context_tokens=64_000,
        effort="high",
    )


def _patch_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent_canary,
        "get_settings",
        lambda: SimpleNamespace(
            claude_agent_sdk_enabled=True,
            agent_runner_image="crucible-agent-runner:base",
        ),
    )
    monkeypatch.setattr(
        agent_canary.agent_runner_manager,
        "image_exists",
        lambda _image=None: True,
    )
    monkeypatch.setattr(
        agent_canary.agent_runner_manager,
        "host_workdir_path",
        lambda task_id: str(tmp_path / task_id),
    )
    monkeypatch.setattr(
        agent_canary.agent_runner_manager,
        "remove_for_task",
        lambda _task_id, _workdir=None: 1,
    )

    async def immediate(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(agent_canary.asyncio, "to_thread", immediate)


@pytest.mark.asyncio
async def test_agent_canary_requires_observed_tools_probe_and_terminal(
    monkeypatch, tmp_path
):
    _patch_runtime(monkeypatch, tmp_path)

    async def fake_run_ai_node(**kwargs):
        workdir = tmp_path / kwargs["task_id"]
        marker = (workdir / "canary" / "marker.txt").read_text(encoding="utf-8")
        (workdir / "canary" / "probe-result.json").write_text(
            json.dumps(
                {
                    "python_ok": True,
                    "credential_visible": False,
                    "visible_names": [],
                }
            ),
            encoding="utf-8",
        )
        for tool in ("Read", "Bash", "mcp__crucible__submit_result"):
            kwargs["on_event"]({"type": "tool.call.started", "tool": tool})
        kwargs["on_event"]({"type": "agent.completed"})
        kwargs["meta_out"].update(
            {
                "num_turns": 3,
                "duration_ms": 25,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
        return {
            "marker": marker,
            "probe_completed": True,
            "credential_visible": False,
            "summary": "ok",
        }

    monkeypatch.setattr(agent_canary, "run_ai_node", fake_run_ai_node)

    result = await agent_canary.run_provider_agent_canary(_provider())

    assert result.ok is True
    assert all(result.checks.model_dump().values())
    assert result.num_turns == 3
    assert result.usage == {"input_tokens": 10, "output_tokens": 5}
    assert result.message == "Agent 兼容测试通过"
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_agent_canary_rejects_model_claim_without_observed_tools(
    monkeypatch, tmp_path
):
    _patch_runtime(monkeypatch, tmp_path)

    async def fake_run_ai_node(**kwargs):
        marker = (
            tmp_path / kwargs["task_id"] / "canary" / "marker.txt"
        ).read_text(encoding="utf-8")
        kwargs["meta_out"].update({"num_turns": 1})
        kwargs["on_event"]({"type": "agent.completed"})
        return {
            "marker": marker,
            "probe_completed": True,
            "credential_visible": False,
            "summary": "claimed ok",
        }

    monkeypatch.setattr(agent_canary, "run_ai_node", fake_run_ai_node)

    result = await agent_canary.run_provider_agent_canary(_provider())

    assert result.ok is False
    assert result.checks.read_tool is False
    assert result.checks.bash_tool is False
    assert result.checks.mcp_submit is False
    assert result.checks.multi_turn is False


@pytest.mark.asyncio
async def test_agent_canary_timeout_forces_container_cleanup(monkeypatch, tmp_path):
    _patch_runtime(monkeypatch, tmp_path)
    removed = agent_canary.asyncio.Event()

    def remove_for_task(_task_id, _workdir=None):
        removed.set()
        return 1

    async def blocked_run_ai_node(**_kwargs):
        await removed.wait()
        raise agent_canary.AgentRunnerError("container removed")

    monkeypatch.setattr(
        agent_canary.agent_runner_manager,
        "remove_for_task",
        remove_for_task,
    )
    monkeypatch.setattr(agent_canary, "run_ai_node", blocked_run_ai_node)
    monkeypatch.setattr(agent_canary, "CANARY_DEADLINE_SECONDS", 0.01)

    result = await agent_canary.run_provider_agent_canary(_provider())

    assert result.ok is False
    assert "超时" in result.message or "超过" in result.message
    assert removed.is_set()
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_agent_canary_failure_preserves_observed_partial_checks(
    monkeypatch, tmp_path
):
    _patch_runtime(monkeypatch, tmp_path)

    async def no_submit(**kwargs):
        workdir = tmp_path / kwargs["task_id"]
        (workdir / "canary" / "probe-result.json").write_text(
            json.dumps(
                {
                    "python_ok": True,
                    "credential_visible": False,
                    "visible_names": [],
                }
            ),
            encoding="utf-8",
        )
        kwargs["on_event"]({"type": "tool.call.started", "tool": "Read"})
        kwargs["on_event"]({"type": "tool.call.started", "tool": "Bash"})
        kwargs["on_event"]({"type": "agent.completed"})
        kwargs["meta_out"].update({"num_turns": 2})
        raise agent_canary.AgentRunnerError(
            "节点 canary 未调用 submit_result(无 .node_output.json)"
        )

    monkeypatch.setattr(agent_canary, "run_ai_node", no_submit)

    result = await agent_canary.run_provider_agent_canary(_provider())

    assert result.ok is False
    assert result.checks.read_tool is True
    assert result.checks.bash_tool is True
    assert result.checks.multi_turn is True
    assert result.checks.credential_isolation is True
    assert result.checks.single_terminal is True
    assert result.checks.mcp_submit is False
    assert "Read" in result.message and "Bash" in result.message


def test_credential_isolation_trusts_probe_not_model_claim():
    """隔离判定只看探针文件；模型误报 credential_visible=true 不得判失败。"""
    checks = agent_canary._observed_checks(
        events=[
            ("tool.call.started", "Read"),
            ("tool.call.started", "Bash"),
            ("tool.call.started", "mcp__crucible__submit_result"),
            ("agent.completed", ""),
        ],
        probe={"python_ok": True, "credential_visible": False},
        meta={"num_turns": 4},
        output={
            "marker": "x",
            "probe_completed": True,
            "credential_visible": True,  # 模型误报
            "summary": "ok",
        },
        marker="x",
    )
    assert checks.credential_isolation is True
    assert checks.mcp_submit is True


def test_read_tool_accepts_read_lineno_prefix_on_marker():
    """弱模型常把 Read 工具的「行号\\t内容」原样填进 marker。"""
    assert (
        agent_canary._normalize_marker_text("1\tcrucible-canary-abc")
        == "crucible-canary-abc"
    )
    assert (
        agent_canary._normalize_marker_text("  12|crucible-canary-abc")
        == "crucible-canary-abc"
    )
    checks = agent_canary._observed_checks(
        events=[
            ("tool.call.started", "Read"),
            ("tool.call.started", "Bash"),
            ("tool.call.started", "mcp__crucible__submit_result"),
            ("agent.completed", ""),
        ],
        probe={"python_ok": True, "credential_visible": False},
        meta={"num_turns": 4},
        output={
            "marker": "1\tcrucible-canary-abc",
            "probe_completed": True,
            "credential_visible": False,
            "summary": "ok",
        },
        marker="crucible-canary-abc",
    )
    assert checks.read_tool is True
