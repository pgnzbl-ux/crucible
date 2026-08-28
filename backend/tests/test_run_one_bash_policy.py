"""run_one.py Bash 策略：节点黑名单 + Provider 凭据清除。"""

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "backend", "agent-runner"))

# 容器入口依赖 SDK；单测只验证 Bash 分类，不真调 query()
sys.modules.setdefault("claude_agent_sdk", MagicMock())

import pytest
from runner.run_one import (
    _allowed_tools_for,
    _bash_command_without_provider_creds,
    _classify_bash,
    _pre_tool_use_hook,
)


@pytest.mark.parametrize(
    "cmd",
    ["npm install", "npx eslint", "pip install flask", "apt-get update", "docker compose up"],
)
def test_env_ready_denies_install_and_docker(cmd):
    decision, reason = _classify_bash(cmd, node_key="env_ready")
    assert decision == "deny"
    assert reason and "blocked by policy" in reason


def test_env_ready_allows_host_docker_internal_in_echo():
    decision, reason = _classify_bash(
        "echo http://host.docker.internal:8080", node_key="env_ready"
    )
    assert decision == "allow", reason


def test_env_ready_allows_node_eval():
    decision, _ = _classify_bash("node -e \"console.log(1)\"", node_key="env_ready")
    assert decision == "allow"


def test_audit_still_allows_npm():
    decision, _ = _classify_bash("npm install", node_key="audit")
    assert decision == "allow"


@pytest.mark.parametrize("cmd", ["curl http://x", "wget http://x", "httpie GET x"])
def test_audit_denies_http_clients(cmd):
    decision, reason = _classify_bash(cmd, node_key="audit")
    assert decision == "deny"
    assert reason and "blocked by policy" in reason


def test_reproduce_still_allows_curl():
    decision, _ = _classify_bash("curl -s http://x", node_key="reproduce")
    assert decision == "allow"


@pytest.mark.parametrize(
    "cmd",
    [
        "docker compose up",
        "docker-compose up",
        "docker ps",
        "sudo docker ps",
        "/usr/bin/docker inspect x",
        "echo ok; docker ps",
    ],
)
def test_reproduce_denies_docker(cmd):
    decision, reason = _classify_bash(cmd, node_key="reproduce")
    assert decision == "deny"
    assert reason and "docker" in reason


@pytest.mark.parametrize(
    "cmd",
    [
        "curl -s http://host.docker.internal:80/",
        'curl -s -o /dev/null -w "%{http_code}\\n" http://host.docker.internal:80/user-login.html',
        "curl -s http://x",
        "echo host.docker.internal",
    ],
)
def test_reproduce_allows_host_docker_internal(cmd):
    """host.docker.internal 含 docker 字样，不得当成 docker CLI 误杀。"""
    decision, reason = _classify_bash(cmd, node_key="reproduce")
    assert decision == "allow", reason


def test_reproduce_keeps_write_and_webfetch():
    tools = _allowed_tools_for("reproduce")
    assert "Write" in tools and "WebFetch" in tools and "Bash" in tools


def test_audit_allowed_tools_are_read_plus_bash_websearch():
    tools = _allowed_tools_for("audit")
    assert tools == ["Read", "Grep", "Glob", "Bash", "WebSearch"]
    assert "Write" not in tools
    assert "WebFetch" not in tools


def test_env_ready_allowed_tools_keep_write():
    tools = _allowed_tools_for("env_ready")
    assert "Write" in tools and "WebFetch" in tools


def test_bash_command_strips_provider_creds_via_env_unset():
    wrapped = _bash_command_without_provider_creds("python /workspace/canary/probe.py")
    assert wrapped.startswith("env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN")
    assert "bash --noprofile --norc -c" in wrapped
    assert "probe.py" in wrapped
    # 幂等：已包装不再套一层
    assert _bash_command_without_provider_creds(wrapped) == wrapped


@pytest.mark.asyncio
async def test_pre_tool_use_rewrites_bash_to_scrub_creds(monkeypatch, capsys):
    monkeypatch.setenv("NODE_KEY", "canary")
    out = await _pre_tool_use_hook(
        {"tool_name": "Bash", "tool_input": {"command": "python probe.py", "timeout": 30}},
        None,
        None,
    )
    assert out is not None
    specific = out["hookSpecificOutput"]
    assert specific["permissionDecision"] == "allow"
    updated = specific["updatedInput"]
    assert updated["timeout"] == 30  # 原字段保留
    assert "env -u ANTHROPIC_API_KEY" in updated["command"]
    logged = capsys.readouterr().out
    assert '"type": "tool.call.scrubbed"' in logged or '"type":"tool.call.scrubbed"' in logged


def test_plain_dict_hooks_silently_drop_under_sdk_hasattr_convert():
    """SDK _convert_hooks_to_internal_format 用 hasattr(m, 'hooks')；裸 dict 会变 hooks=[]。"""
    matcher = {"matcher": "Bash", "hooks": [object()]}
    internal = {
        "matcher": matcher.matcher if hasattr(matcher, "matcher") else None,
        "hooks": matcher.hooks if hasattr(matcher, "hooks") else [],
    }
    assert internal["matcher"] is None
    assert internal["hooks"] == []


def test_build_options_registers_hook_matcher_surviving_sdk_convert(monkeypatch):
    """注册必须是 HookMatcher 实例，不能是裸 dict。"""
    from dataclasses import dataclass, field
    from pathlib import Path

    captured: dict = {}

    class CaptureOptions:
        def __init__(self, **kwargs):
            captured.clear()
            captured.update(kwargs)

    @dataclass
    class FakeHookMatcher:
        matcher: str | None = None
        hooks: list = field(default_factory=list)
        timeout: float | None = None

    import runner.run_one as run_one

    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv(
        "NODE_SKILL_DIR",
        str(root / "backend" / "agent-runner" / "node-skills"),
    )
    run_one.ClaudeAgentOptions = CaptureOptions
    run_one.HookMatcher = FakeHookMatcher
    run_one._build_options(model="m", max_turns=4, node_key="canary", cwd="/workspace")

    pre = captured["hooks"]["PreToolUse"]
    assert len(pre) == 3, "Bash 黑名单 + Read/Grep 压缩产物拦截"
    assert all(isinstance(m, FakeHookMatcher) for m in pre)
    assert not any(isinstance(m, dict) for m in pre)
    by_matcher = {m.matcher: m for m in pre}
    assert by_matcher["Bash"].hooks == [run_one._pre_tool_use_hook]
    assert by_matcher["Read"].hooks == [run_one._read_guard_hook]
    assert by_matcher["Grep"].hooks == [run_one._read_guard_hook]

    stop = captured["hooks"]["Stop"]
    assert len(stop) == 1
    assert isinstance(stop[0], FakeHookMatcher)
    assert not isinstance(stop[0], dict)
    assert stop[0].matcher is None
    assert stop[0].hooks == [run_one._stop_hook]

    m = pre[0]
    internal = {
        "matcher": m.matcher if hasattr(m, "matcher") else None,
        "hooks": m.hooks if hasattr(m, "hooks") else [],
    }
    assert internal["matcher"] == "Bash"
    assert internal["hooks"] == [run_one._pre_tool_use_hook]

