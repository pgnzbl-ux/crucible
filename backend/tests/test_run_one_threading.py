"""run_one._stream_messages：主/子代理线程字段、结果补名与子代理生命周期。"""
import os
import sys
import types
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

from runner import run_one  # noqa: E402


# ---- 可 isinstance 的 SDK 消息占位类：覆写模块符号，避免测试顺序耦合 ----

class FSystem(SimpleNamespace):
    pass


class FAssistant(SimpleNamespace):
    pass


class FUser(SimpleNamespace):
    pass


class FResult(SimpleNamespace):
    pass


class FText(SimpleNamespace):
    pass


class FToolUse(SimpleNamespace):
    pass


class FToolResult(SimpleNamespace):
    pass


def _install():
    run_one.SystemMessage = FSystem
    run_one.AssistantMessage = FAssistant
    run_one.UserMessage = FUser
    run_one.ResultMessage = FResult
    run_one.TextBlock = FText
    run_one.ToolUseBlock = FToolUse
    run_one.ToolResultBlock = FToolResult


_install()


async def _collect(messages):
    async def fake_query(prompt, options):
        for m in messages:
            yield m

    original_query = run_one.query
    run_one.query = fake_query
    try:
        return [ev async for ev in run_one._stream_messages(None, "", None)]
    finally:
        run_one.query = original_query


@pytest.mark.asyncio
async def test_threading_and_completion_decoration():
    """parent_tool_use_id 全事件透传；Task 子代理内层 Bash 结果补 tool+command。"""

    messages = [
        # 子代理生命周期（SDK SystemMessage）
        FSystem(
            subtype="task_started",
            data={"task_id": "tu_1", "description": "族 A 二审"},
        ),
        # 主线程：思考 + 派发 Task 子代理
        FAssistant(
            parent_tool_use_id=None,
            model="m1",
            session_id="s1",
            content=[
                FText(text="正文"),
                FToolUse(id="tu_1", name="Task", input={"description": "族 A 二审"}),
            ],
        ),
        # 子代理线程内：Bash 调用 + 其结果
        FAssistant(
            parent_tool_use_id="tu_1",
            model="m1",
            session_id="s1",
            content=[FToolUse(id="b1", name="Bash", input={"command": "ls -l"})],
        ),
        FUser(
            parent_tool_use_id="tu_1",
            session_id="s1",
            content=[FToolResult(tool_use_id="b1", content="a.py\nb.py", is_error=False)],
        ),
    ]

    events = await _collect(messages)
    by_type: dict[str, list[dict]] = {}
    for ev in events:
        by_type.setdefault(ev["type"], []).append(ev)

    sub = by_type["agent.subagent.updated"]
    assert len(sub) == 1
    assert sub[0]["tool_use_id"] == "tu_1"
    assert sub[0]["label"] == "族 A 二审"

    started = {ev["tool_use_id"]: ev for ev in by_type["tool.call.started"]}
    assert started["b1"]["parent_tool_use_id"] == "tu_1"
    assert started["tu_1"]["tool"] == "Task"
    assert started["tu_1"]["parent_tool_use_id"] is None

    completed = {ev["tool_use_id"]: ev for ev in by_type["tool.call.completed"]}
    bash_done = completed["b1"]
    assert bash_done["tool"] == "Bash", "started 登记应回填工具名"
    assert bash_done["command"] == "ls -l"
    assert bash_done["parent_tool_use_id"] == "tu_1"
    # 思考/文本行带线程字段且默认主线程为 None
    assert all("parent_tool_use_id" in ev for ev in events), events[:3]


@pytest.mark.asyncio
async def test_thinking_tokens_heartbeat_still_dropped():
    """既有噪声过滤不受影响：thinking_tokens 心跳不产生事件。"""
    messages = [FSystem(subtype="system", data={"thinking_tokens": 1})]
    events = await _collect(messages)
    assert events == []


@pytest.mark.asyncio
async def test_tool_result_content_list_of_dicts_is_joined():
    """SDK 0.2.x：ToolResultBlock.content 可为 dict 块列表（Agent 工具结果恒为此形态）。

    旧实现用 hasattr(b,'text') 提取，dict 全部落空 → 事件 output 变空串，
    前端显示"无输出内容"。必须同时兼容 dict 与对象两种块形态，跳过 image 块。
    """
    messages = [
        FAssistant(
            parent_tool_use_id=None,
            model="m1",
            session_id="s1",
            content=[FToolUse(id="ag1", name="Agent", input={"description": "族 A"})],
        ),
        FUser(
            parent_tool_use_id=None,
            session_id="s1",
            content=[
                FToolResult(
                    tool_use_id="ag1",
                    content=[
                        {"type": "text", "text": "子代理结论一"},
                        {"type": "image", "source": {"data": "..."}},  # 非 text 块跳过
                        {"type": "text", "text": "子代理结论二"},
                    ],
                    is_error=False,
                ),
            ],
        ),
    ]

    events = await _collect(messages)
    done = [e for e in events if e["type"] == "tool.call.completed"]
    assert len(done) == 1
    assert done[0]["output"] == "子代理结论一 子代理结论二"
    assert done[0]["tool"] == "Agent", "started 侧登记应回填工具名"


@pytest.mark.asyncio
async def test_tool_result_content_plain_string_still_works():
    """旧形态（纯字符串 content）回归保护。"""
    messages = [
        FUser(
            parent_tool_use_id=None,
            session_id="s1",
            content=[FToolResult(tool_use_id="b1", content="plain text", is_error=False)],
        ),
    ]
    events = await _collect(messages)
    assert events[0]["output"] == "plain text"
