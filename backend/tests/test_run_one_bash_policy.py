"""runner.policies Bash 策略：容器逃逸拦截 + Provider 凭据剥离。"""

import os
import sys
import types
from unittest.mock import MagicMock

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(os.path.dirname(root), "backend", "agent-runner"))

# 容器入口依赖 SDK；单测只验证 Bash 分类，不真调 query()
sys.modules.setdefault("claude_agent_sdk", MagicMock())


import pytest  # noqa: E402
from runner.policies import (  # noqa: E402
    bash_command_without_provider_creds,
    build_policy_hooks,
    classify_bash,
    make_bash_policy_hook,
)


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


@pytest.mark.parametrize(
    "cmd",
    [
        "cat script.sh | bash",
        "sh run.sh",
        "rm -rf /tmp/test",
        "mv a.txt b.txt",
        "cp -r /workspace/src /tmp/bak",
        "npm install",
        "npx eslint",
        "pip install flask",
        "apt-get update",
        "curl -s http://example.com",
        "wget -q http://example.com",
    ],
)
def test_sandbox_allows_full_linux_and_bash_tools(cmd):
    """沙箱容器内全面放开各种 Linux 管道、构建依赖、网络工具与文件操作。"""
    decision, reason = classify_bash(cmd)
    assert decision == "allow", reason


def test_allows_host_docker_internal_in_echo():
    decision, reason = classify_bash("echo http://host.docker.internal:8080")
    assert decision == "allow", reason


def test_allows_node_eval():
    decision, _ = classify_bash("node -e \"console.log(1)\"")
    assert decision == "allow"


def test_allows_curl_and_npm():
    assert classify_bash("curl http://x")[0] == "allow"
    assert classify_bash("npm install")[0] == "allow"


@pytest.mark.parametrize(
    "cmd",
    [
        "docker compose up",
        "docker-compose up",
        "docker ps",
        "sudo docker ps",
        "/usr/bin/docker inspect x",
        "echo ok; docker ps",
        "podman run alpine",
    ],
)
def test_strictly_deny_docker_escape(cmd):
    """严格拦截 docker/podman 容器逃逸命令（正则第一道闸，容器隔离是真实边界）。"""
    decision, reason = classify_bash(cmd)
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
def test_allows_host_docker_internal(cmd):
    """host.docker.internal 含 docker 字样，不得当成 docker CLI 误杀。"""
    decision, reason = classify_bash(cmd)
    assert decision == "allow", reason


def test_base_allowed_tools_cover_full_toolchain():
    from runner.gateway import BASE_ALLOWED_TOOLS

    assert "Write" in BASE_ALLOWED_TOOLS
    assert "WebFetch" in BASE_ALLOWED_TOOLS
    assert "Bash" in BASE_ALLOWED_TOOLS
    assert "Read" in BASE_ALLOWED_TOOLS


def test_bash_command_strips_provider_creds_via_env_unset():
    wrapped = bash_command_without_provider_creds("python /workspace/canary/probe.py")
    assert wrapped.startswith("env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN")
    assert "bash --noprofile --norc -c" in wrapped
    assert "probe.py" in wrapped
    # 幂等：已包装不再套一层
    assert bash_command_without_provider_creds(wrapped) == wrapped


@pytest.mark.asyncio
async def test_bash_policy_hook_rewrites_command_and_emits_scrubbed_event():
    events: list[dict] = []
    hook = make_bash_policy_hook(events.append)
    out = await hook(
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
    # 审计事件经 EventSink 进事件流（server 模式审计链不断裂）
    assert [e["type"] for e in events] == ["tool.call.scrubbed"]
    assert events[0]["tool"] == "Bash"


@pytest.mark.asyncio
async def test_bash_policy_hook_denies_docker_and_emits_denied_event():
    events: list[dict] = []
    hook = make_bash_policy_hook(events.append)
    out = await hook({"tool_name": "Bash", "tool_input": {"command": "docker ps"}}, None, None)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert events and events[0]["type"] == "tool.call.denied"


def test_plain_dict_hooks_silently_drop_under_sdk_hasattr_convert():
    """SDK _convert_hooks_to_internal_format 用 hasattr(m, 'hooks')；裸 dict 会变 hooks=[]。"""
    matcher = {"matcher": "Bash", "hooks": [object()]}
    internal = {
        "matcher": matcher.matcher if hasattr(matcher, "matcher") else None,
        "hooks": matcher.hooks if hasattr(matcher, "hooks") else [],
    }
    assert internal["matcher"] is None
    assert internal["hooks"] == []


def test_build_policy_hooks_registers_hook_matcher_surviving_sdk_convert():
    """注册必须是 HookMatcher 实例，不能是裸 dict（裸 dict 被 SDK 静默丢弃）。"""
    from dataclasses import dataclass, field

    @dataclass
    class FakeHookMatcher:
        matcher: str | None = None
        hooks: list = field(default_factory=list)
        timeout: float | None = None

    # build_policy_hooks 在调用时 import HookMatcher：注入带真类的 stub 模块
    stub = types.ModuleType("claude_agent_sdk")
    stub.HookMatcher = FakeHookMatcher
    sys.modules["claude_agent_sdk"] = stub

    events: list[dict] = []
    hooks = build_policy_hooks(
        events.append, workspace_root="/workspace", output_path="/workspace/.out.json", submit_enforced=True
    )

    pre = hooks["PreToolUse"]
    assert len(pre) == 4, "Bash 黑名单 + Read/Grep/Edit 压缩产物拦截"
    assert all(isinstance(m, FakeHookMatcher) for m in pre)
    assert not any(isinstance(m, dict) for m in pre)
    by_matcher = {m.matcher: m for m in pre}
    assert by_matcher["Bash"].hooks[0].__name__ == "bash_policy_hook"
    assert by_matcher["Read"].hooks[0].__name__ == "read_guard_hook"
    assert by_matcher["Grep"].hooks[0].__name__ == "read_guard_hook"
    assert by_matcher["Edit"].hooks[0].__name__ == "read_guard_hook"

    stop = hooks["Stop"]
    assert len(stop) == 1
    assert isinstance(stop[0], FakeHookMatcher)
    assert stop[0].matcher is None
    assert stop[0].hooks[0].__name__ == "stop_hook"

    m = pre[0]
    internal = {
        "matcher": m.matcher if hasattr(m, "matcher") else None,
        "hooks": m.hooks if hasattr(m, "hooks") else [],
    }
    assert internal["matcher"] == "Bash"
    assert internal["hooks"] == [by_matcher["Bash"].hooks[0]]


def test_build_policy_hooks_omits_stop_when_no_submit_contract():
    """无 submit 契约（spec.submit_schema 缺省）时不注册 Stop 催交。"""
    stub = types.ModuleType("claude_agent_sdk")
    stub.HookMatcher = MagicMock()
    sys.modules["claude_agent_sdk"] = stub
    hooks = build_policy_hooks(
        lambda e: None, workspace_root="/workspace", output_path="/workspace/.out.json",
        submit_enforced=False,
    )
    assert "Stop" not in hooks
