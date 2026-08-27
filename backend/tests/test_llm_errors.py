"""LLM API 错误识别（容器 / worker 共用语义）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.contexts.agent.llm_errors import (
    classify_llm_api_error,
    is_fatal_llm_error,
    is_llm_api_failure,
)


@pytest.mark.parametrize(
    "raw,title_part",
    [
        (
            'HTTP 401: {"error":{"code":"1004","message":"余额不足"}}',
            "余额不足",
        ),
        (
            "AI 节点 audit LLM 调用失败: HTTP 401: 余额不足",
            "余额不足",
        ),
        (
            "Claude Code returned an error result: success",
            "会话异常结束",
        ),
        (
            "HTTP 429: rate limit exceeded",
            "限流",
        ),
        (
            "API Error: 500 Context size has been exceeded. gateway overloaded",
            "上下文窗口不足",
        ),
        (
            "API Error: 503 upstream unavailable",
            "暂时不可用",
        ),
    ],
)
def test_classify_llm_api_error(raw, title_part):
    out = classify_llm_api_error(raw)
    assert out is not None
    assert title_part in out[0] or title_part in out[1]


def test_is_llm_api_failure_balance():
    assert is_llm_api_failure('HTTP 401: {"error":{"code":"1004","message":"余额不足"}}')


def test_is_llm_api_failure_accepts_gateway_api_error_format():
    assert is_llm_api_failure("API Error: 500 Context size has been exceeded")


@pytest.mark.parametrize(
    "raw,fatal",
    [
        ('HTTP 401: {"error":{"message":"invalid api key"}}', True),
        ("AI 节点 triage LLM 调用失败: API Error: 403 forbidden", True),
        ('HTTP 401: {"error":{"code":"1004","message":"余额不足"}}', True),
        ("API Error: 404 model_not_found: no such model", True),
        ("API Error: 400 prompt is too long", True),
        # 瞬时类：重试/降级处理，不得中止节点
        ("AI 节点 triage LLM 调用失败: API Error: 502 upstream unavailable", False),
        ("API Error: 500 Internal Server Error", False),
        ("HTTP 429: rate limit exceeded", False),
        ("API Error: Connection closed mid-response", False),
        ("Claude Code returned an error result: success", False),
        # 平台侧预检文案（含 git clone），不是 LLM 错误
        ("源码克隆失败: HTTP 401 repository not found", False),
        ("", False),
        (None, False),
    ],
)
def test_is_fatal_llm_error(raw, fatal):
    assert is_fatal_llm_error(raw) is fatal


def test_humanize_prefers_balance_over_no_submit():
    from app.contexts.agent.errors import humanize_agent_error

    raw = 'AI 节点 audit 未产出 .node_output.json (exit=1): HTTP 401: {"error":{"code":"1004","message":"余额不足"}}'
    title, hint = humanize_agent_error(raw)
    assert "余额不足" in title
    assert "submit_result" not in title


@pytest.mark.asyncio
async def test_missing_output_prefers_llm_failure_event(tmp_path, monkeypatch):
    """较早的 LLM 401 不能被末尾 no_submit 覆盖。"""
    from unittest.mock import MagicMock, patch

    from app.contexts.agent import ai_runner
    from app.core.agent_runner import AgentRunnerError

    settings = MagicMock()
    settings.claude_agent_sdk_enabled = True
    events = [
        {
            "type": "agent.failed",
            "error": 'HTTP 401: {"error":{"code":"1004","message":"余额不足"}}',
        },
        {
            "type": "agent.failed",
            "error": "节点 audit 未调用 submit_result(无 .node_output.json)",
        },
    ]

    def _fake_run(spec, on_event):
        for ev in events:
            on_event(ev)
        return 1, {"stderr_tail": "", "timed_out": False}

    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    with patch("app.contexts.agent.ai_runner.agent_runner_manager") as mgr:
        mgr.run_with_streaming.side_effect = _fake_run
        with pytest.raises(AgentRunnerError) as ei:
            await ai_runner.run_ai_node(
                node_key="audit",
                input_json={},
                host_workdir=str(tmp_path),
                runner_env={},
            )
    msg = str(ei.value)
    assert "LLM 调用失败" in msg
    assert "余额不足" in msg
    assert "未产出 .node_output.json" not in msg
