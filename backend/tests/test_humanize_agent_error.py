"""Agent 错误文案：子串匹配 → 人类可读标题。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.contexts.agent.errors import humanize_agent_error


@pytest.mark.parametrize(
    "raw,expect_title_part",
    [
        ("AI 节点 env_ready 未产出 .node_output.json (exit=1)", "没有提交节点结果"),
        ("AI 节点 audit output 校验失败: 缺必需字段: gate_verdict", "缺必填字段"),
        ("源码克隆失败: Authentication failed", "克隆源码失败"),
        ("agent-runner 镜像不存在: crucible-agent-runner:base", "缺少 agent-runner 镜像"),
        ("缺少 LLM 凭据：DB 默认 Provider 与 settings.llm_api_key 都为空", "没有可用的 LLM"),
        ("靶场搭建 5 轮全失败: attempt 5 compose up 失败", "5 轮排障"),
        ("AI 节点 reproduce 超时(1800s)", "超时"),
        ("节点 audit 未调用 submit_result(无 .node_output.json)", "没有提交节点结果"),
    ],
)
def test_humanize_known_errors(raw, expect_title_part):
    title, hint = humanize_agent_error(raw)
    assert expect_title_part in title
    assert len(hint) > 10


def test_humanize_unknown_keeps_text():
    title, hint = humanize_agent_error("weird boom xyz")
    assert "weird boom xyz" in title
    assert "事件流" in hint


def test_format_agent_error_includes_next_step():
    from app.contexts.agent.errors import format_agent_error

    text = format_agent_error("AI 节点 env_ready 未产出 .node_output.json", node_key="env_ready")
    assert "env_ready" in text
    assert "下一步:" in text
    assert "原因:" in text
