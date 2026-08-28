"""runner.gateway：thinking 抽取 + usage 序列化 + 失败事件与终态唯一性。"""

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
        "backend",
        "agent-runner",
    ),
)

sys.modules.setdefault("claude_agent_sdk", MagicMock())


@pytest.fixture(autouse=True)
def _sdk_module_stub():
    """每个测试独立的 claude_agent_sdk stub，测试后还原，杜绝跨文件全局污染。"""
    prev = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = MagicMock()
    yield
    if prev is None:
        sys.modules.pop("claude_agent_sdk", None)
    else:
        sys.modules["claude_agent_sdk"] = prev


import runner.gateway as gateway  # noqa: E402
from runner.schemas import RUNNER_EXIT_EVENT, AgentSpec  # noqa: E402


def test_usage_jsonable_converts_objects_for_sidecar():
    """usage / model_usage 若是对象，sidecar 不得 default=str 成不可解析字符串。"""
    import json

    obj = SimpleNamespace(inputTokens=12, outputTokens=3, cacheReadInputTokens=40)
    blob = gateway.usage_jsonable({"deepseek-v4-flash": obj})
    dumped = json.dumps(blob)
    parsed = json.loads(dumped)
    assert parsed["deepseek-v4-flash"]["inputTokens"] == 12
    assert parsed["deepseek-v4-flash"]["outputTokens"] == 3
    assert parsed["deepseek-v4-flash"]["cacheReadInputTokens"] == 40
    assert isinstance(gateway.usage_jsonable(obj), dict)


def test_extract_thinking_from_attr():
    block = SimpleNamespace(thinking="先读 README")
    assert gateway.extract_thinking_text(block) == "先读 README"


def test_extract_thinking_from_type_name():
    class FakeThinkingBlock:
        text = "推演 payload"

    assert gateway.extract_thinking_text(FakeThinkingBlock()) == "推演 payload"


def test_extract_thinking_from_dict():
    assert gateway.extract_thinking_text({"type": "thinking", "thinking": "检查鉴权"}) == "检查鉴权"
    assert gateway.extract_thinking_text({"type": "text", "text": "hi"}) is None


def test_extract_thinking_ignores_empty():
    assert gateway.extract_thinking_text(None) is None
    assert gateway.extract_thinking_text(SimpleNamespace(text="not thinking")) is None


def test_humanize_provider_error_is_transport_level():
    """Provider 错误给中性文案；余额充值等运营指引归 backend llm_errors。"""
    title, hint = gateway.humanize_container_error(
        'HTTP 401: {"error":{"code":"1004","message":"余额不足"}}'
    )
    assert "Provider" in title or "LLM" in title
    assert hint


def test_humanize_container_submit_result():
    title, hint = gateway.humanize_container_error("任务 audit 未调用 submit_result（无提交产物）")
    assert "提交" in title
    assert "重试" in hint


def test_humanize_container_env_incomplete():
    for raw in (
        "bubblewrap is required for subprocess env scrubbing and isolation",
        "Sandbox dependencies not available: socat not installed",
        "bwrap: No permissions to create new namespace",
    ):
        title, hint = gateway.humanize_container_error(raw)
        assert "运行环境" in title
        assert "重建" in hint


def test_failed_event_carries_title_hint():
    ev = gateway.failed_event("claude_agent_sdk 导入失败: boom", sequence=1)
    assert ev["type"] == "agent.failed"
    assert ev["title"]
    assert ev["hint"]
    assert ev["code"] == "INFRA_DEPENDENCY_MISSING"
    assert "导入失败" in ev["error"]


def test_system_init_emits_start_phase():
    msg = SimpleNamespace(subtype="init", session_id="s1", parent_tool_use_id=None)
    events = gateway.translate_system(msg, sid="s1", parent=None)
    assert len(events) == 1
    assert events[0]["type"] == "phase.updated"
    assert events[0]["phase"] == "start"
    assert events[0]["message"] == "init"


def test_system_thinking_tokens_is_dropped():
    msg = SimpleNamespace(subtype="thinking_tokens", parent_tool_use_id=None)
    assert gateway.translate_system(msg, sid=None, parent=None) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_error", "expected_terminal"),
    [(False, "agent.completed"), (True, "agent.failed")],
)
async def test_result_message_emits_exactly_one_terminal_event(monkeypatch, tmp_path, is_error, expected_terminal):
    class FakeSystem:
        pass

    class FakeAssistant:
        pass

    class FakeUser:
        pass

    class FakeResult(SimpleNamespace):
        pass

    class FakeText:
        pass

    class FakeToolUse:
        pass

    class FakeToolResult:
        pass

    result = FakeResult(
        result="provider failed" if is_error else "done",
        is_error=is_error,
        session_id="session-1",
        duration_ms=10,
        total_cost_usd=0,
        num_turns=1,
        usage={"input_tokens": 1, "output_tokens": 1},
        model_usage=None,
        parent_tool_use_id=None,
    )

    async def fake_query(*, prompt, options):
        yield result

    monkeypatch.setattr(gateway, "SDK_IMPORT_ERROR", None)
    monkeypatch.setattr(gateway, "SystemMessage", FakeSystem)
    monkeypatch.setattr(gateway, "AssistantMessage", FakeAssistant)
    monkeypatch.setattr(gateway, "UserMessage", FakeUser)
    monkeypatch.setattr(gateway, "ResultMessage", FakeResult)
    monkeypatch.setattr(gateway, "TextBlock", FakeText)
    monkeypatch.setattr(gateway, "ToolUseBlock", FakeToolUse)
    monkeypatch.setattr(gateway, "ToolResultBlock", FakeToolResult)
    monkeypatch.setattr(gateway, "query", fake_query)
    monkeypatch.setenv("ANTHROPIC_MODEL", "test-model")

    spec = AgentSpec(
        node_key="audit",
        node_payload={"source_path": str(tmp_path)},
        workspace_root=str(tmp_path),
        transcript_path=str(tmp_path / "transcript.jsonl"),
        meta_path=str(tmp_path / "node_meta.json"),
        output_path=str(tmp_path / "node_output.json"),
    )
    events = [event async for event in gateway.run_spec(spec)]
    terminals = [
        event
        for event in events
        if event["type"] in {"agent.failed", "agent.completed"}
    ]

    assert [event["type"] for event in terminals] == [expected_terminal]
    # 终帧恒为 runner.exit：失败→1，成功→0
    exit_event = events[-1]
    assert exit_event["type"] == RUNNER_EXIT_EVENT
    assert exit_event["exit_code"] == (1 if is_error else 0)
    # sequence 单调递增
    seqs = [e["sequence"] for e in events]
    assert seqs == sorted(seqs)
    # sidecar 已写
    assert (tmp_path / "node_meta.json").is_file()
