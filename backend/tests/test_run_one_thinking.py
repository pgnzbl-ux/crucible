"""run_one.py：thinking 抽取 + 容器失败文案。"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "infrastructure",
        "agent-runner",
    ),
)

sys.modules.setdefault("claude_agent_sdk", MagicMock())

from runner.run_one import (  # noqa: E402
    _failed_event,
    _system_phase_event,
    extract_thinking_text,
    humanize_container_error,
)


def test_extract_thinking_from_attr():
    block = SimpleNamespace(thinking="先读 README")
    assert extract_thinking_text(block) == "先读 README"


def test_extract_thinking_from_type_name():
    class FakeThinkingBlock:
        text = "推演 payload"

    assert extract_thinking_text(FakeThinkingBlock()) == "推演 payload"


def test_extract_thinking_from_dict():
    assert extract_thinking_text({"type": "thinking", "thinking": "检查鉴权"}) == "检查鉴权"
    assert extract_thinking_text({"type": "text", "text": "hi"}) is None


def test_extract_thinking_ignores_empty():
    assert extract_thinking_text(None) is None
    assert extract_thinking_text(SimpleNamespace(text="not thinking")) is None


def test_humanize_container_balance_insufficient():
    title, hint = humanize_container_error('HTTP 401: {"error":{"code":"1004","message":"余额不足"}}')
    assert "余额不足" in title
    assert "充值" in hint


def test_humanize_container_submit_result():
    title, hint = humanize_container_error("节点 audit 未调用 submit_result(无 .node_output.json)")
    assert "提交节点结果" in title
    assert "重试" in hint or "Agent 测试" in hint


def test_humanize_container_bubblewrap_missing():
    title, hint = humanize_container_error(
        "bubblewrap is required for subprocess env scrubbing and isolation"
    )
    assert "进程隔离依赖" in title
    assert "bubblewrap" in hint


def test_humanize_container_socat_missing():
    title, hint = humanize_container_error(
        "Sandbox dependencies not available: socat not installed"
    )
    assert "沙箱运行依赖" in title
    assert "socat" in hint


def test_humanize_container_namespace_blocked():
    title, hint = humanize_container_error(
        "bwrap: No permissions to create new namespace"
    )
    assert "嵌套沙箱" in title
    assert "seccomp" in hint


def test_failed_event_carries_title_hint():
    ev = _failed_event("claude_agent_sdk 导入失败: boom", sequence=1)
    assert ev["type"] == "agent.failed"
    assert ev["title"]
    assert ev["hint"]
    assert "导入失败" in ev["error"]


def test_system_init_emits_start_phase():
    msg = SimpleNamespace(subtype="init", session_id="s1")
    ev = _system_phase_event(msg, seq=1, timestamp=1.0, session_id="s1")
    assert ev is not None
    assert ev["type"] == "phase.updated"
    assert ev["phase"] == "start"
    assert ev["message"] == "init"


def test_system_thinking_tokens_is_dropped():
    msg = SimpleNamespace(subtype="thinking_tokens")
    assert _system_phase_event(msg, seq=2, timestamp=1.0, session_id=None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_error", "expected_type"),
    [(False, "agent.completed"), (True, "agent.failed")],
)
async def test_result_message_emits_exactly_one_terminal_event(
    monkeypatch,
    is_error,
    expected_type,
):
    import runner.run_one as run_one

    class FakeSystem:
        pass

    class FakeAssistant:
        pass

    class FakeUser:
        pass

    class FakeResult:
        def __init__(self):
            self.result = "provider failed" if is_error else "done"
            self.is_error = is_error
            self.session_id = "session-1"
            self.duration_ms = 10
            self.total_cost_usd = 0
            self.num_turns = 1
            self.usage = {"input_tokens": 1, "output_tokens": 1}

    async def fake_query(*, prompt, options):
        yield FakeResult()

    monkeypatch.setattr(run_one, "SystemMessage", FakeSystem)
    monkeypatch.setattr(run_one, "AssistantMessage", FakeAssistant)
    monkeypatch.setattr(run_one, "UserMessage", FakeUser)
    monkeypatch.setattr(run_one, "ResultMessage", FakeResult)
    monkeypatch.setattr(run_one, "query", fake_query)

    events = [event async for event in run_one._stream_messages(options=object(), prompt="x", node_key="audit")]
    terminals = [event for event in events if event["type"] in {"agent.failed", "agent.completed"}]

    assert [event["type"] for event in terminals] == [expected_type]
