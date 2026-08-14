"""ai_runner output 校验测试(不起真容器)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.contexts.agent.ai_runner import (
    NODE_INPUT_SCHEMAS,
    rewrite_url_for_agent_container,
    validate_output,
)


def test_validate_env_ready_ok():
    ok, err = validate_output(
        "env_ready",
        {"target_url": "http://x:8080", "compose_path": ".vuln-env/docker-compose.yml"},
    )
    assert ok and err is None


def test_validate_env_ready_only_target_required():
    """env_ready: target_url + compose_path 都 required。"""
    ok, err = validate_output("env_ready", {"target_url": "http://x:8080"})
    assert not ok  # 缺 compose_path
    assert "compose_path" in err
    # 补上 compose_path 才 ok
    ok2, _ = validate_output("env_ready", {"target_url": "http://x:8080", "compose_path": "x.yml"})
    assert ok2


def test_validate_env_ready_no_target():
    ok, err = validate_output("env_ready", {"compose_path": "x"})
    assert not ok
    assert "target_url" in err


def test_validate_audit_gate_verdict():
    ok, _ = validate_output("audit", {"gate_verdict": "fail"})
    assert ok


def test_validate_audit_no_gate():
    ok, err = validate_output("audit", {"kill_chain": "..."})
    assert not ok
    assert "gate_verdict" in err


def test_validate_report():
    ok, _ = validate_output("report", {"report_data": {"x": 1}, "final_verdict": "confirmed"})
    assert ok


def test_validate_report_missing():
    ok, err = validate_output("report", {"report_data": {}})
    assert not ok
    assert "final_verdict" in err


def test_validate_profile_requires_is_web():
    ok, err = validate_output("profile", {"language": "python"})
    assert not ok
    assert "is_web" in (err or "")
    ok2, _ = validate_output(
        "profile",
        {"is_web": True, "language": "python", "framework": "fastapi"},
    )
    assert ok2


def test_input_schemas_defined_for_all_ai_nodes():
    """5 个 AI 节点都有 submit_result input schema。"""
    for key in ["profile", "env_ready", "audit", "reproduce", "report"]:
        assert key in NODE_INPUT_SCHEMAS, f"{key} 缺 input schema"
        assert "required" in NODE_INPUT_SCHEMAS[key]


def test_rewrite_localhost_to_host_docker_internal():
    assert rewrite_url_for_agent_container("http://localhost:8080") == "http://host.docker.internal:8080"
    assert rewrite_url_for_agent_container("http://127.0.0.1:5000/login") == "http://host.docker.internal:5000/login"
    assert rewrite_url_for_agent_container("http://example.com") == "http://example.com"
    assert rewrite_url_for_agent_container(None) is None


@pytest.mark.asyncio
async def test_missing_output_includes_failed_event(tmp_path, monkeypatch):
    """容器 exit=1 且没写 .node_output.json 时，错误必须带上 JSONL 失败事件。

    回归：stderr 为空时用户只看到「未产出 .node_output.json」，真正原因在 stdout JSONL。
    """
    from unittest.mock import MagicMock, patch

    from app.contexts.agent import ai_runner
    from app.core.agent_runner import AgentRunnerError

    settings = MagicMock()
    settings.claude_agent_sdk_enabled = True

    def _fake_run(spec, on_event):
        on_event({
            "type": "agent.failed",
            "error": "节点 env_ready 未调用 submit_result(无 .node_output.json)",
            "title": "Agent 没有提交节点结果就结束了",
        })
        return 1, {"stderr_tail": "", "timed_out": False}

    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    with patch("app.contexts.agent.ai_runner.agent_runner_manager") as mgr:
        mgr.run_with_streaming.side_effect = _fake_run
        with pytest.raises(AgentRunnerError) as ei:
            await ai_runner.run_ai_node(
                node_key="env_ready",
                input_json={"attempt": 1},
                host_workdir=str(tmp_path),
                runner_env={},
            )
    msg = str(ei.value)
    assert "未产出 .node_output.json" in msg
    assert "未调用 submit_result" in msg


@pytest.mark.asyncio
async def test_missing_output_includes_stderr_runner_import_error(tmp_path, monkeypatch):
    """python -m runner.run_one 找不到模块时，错误必须带上 stderr。"""
    from unittest.mock import MagicMock, patch

    from app.contexts.agent import ai_runner
    from app.core.agent_runner import AgentRunnerError

    settings = MagicMock()
    settings.claude_agent_sdk_enabled = True
    stderr = (
        "/usr/local/bin/python: Error while finding module specification for "
        "'runner.run_one' (ModuleNotFoundError: No module named 'runner')"
    )

    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    with patch("app.contexts.agent.ai_runner.agent_runner_manager") as mgr:
        mgr.run_with_streaming.return_value = (1, {"stderr_tail": stderr, "timed_out": False})
        with pytest.raises(AgentRunnerError) as ei:
            await ai_runner.run_ai_node(
                node_key="env_ready",
                input_json={"attempt": 1},
                host_workdir=str(tmp_path),
                runner_env={},
            )
    assert "No module named 'runner'" in str(ei.value)
