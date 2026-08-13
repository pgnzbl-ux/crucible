"""run_one.py：thinking 抽取 + 容器失败文案。"""
import sys
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

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

from runner.run_one import extract_thinking_text, humanize_container_error, _failed_event  # noqa: E402


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


def test_humanize_container_submit_result():
    title, hint = humanize_container_error("节点 audit 未调用 submit_result(无 .node_output.json)")
    assert "提交节点结果" in title
    assert "submit_result" in hint


def test_failed_event_carries_title_hint():
    ev = _failed_event("claude_agent_sdk 导入失败: boom", sequence=1)
    assert ev["type"] == "agent.failed"
    assert ev["title"]
    assert ev["hint"]
    assert "导入失败" in ev["error"]
