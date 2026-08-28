"""agent-runner HTTP/SSE 驱动：summary 诊断、SSE 解析、容器编排安全参数。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch

import pytest

from app.core.agent_runner import (
    AGENT_EXTRA_HOSTS,
    AgentRunnerError,
    AgentRunnerManager,
    AgentRunnerSpec,
    _iter_sse_events,
)


def test_extra_hosts_empty_without_host_docker_internal():
    assert AGENT_EXTRA_HOSTS == {}
    assert "host.docker.internal" not in AGENT_EXTRA_HOSTS


def test_runner_uses_public_dns_servers():
    from app.core.agent_runner import AGENT_RUNNER_DNS

    assert "8.8.8.8" in AGENT_RUNNER_DNS
    assert "1.1.1.1" in AGENT_RUNNER_DNS
    assert "223.5.5.5" in AGENT_RUNNER_DNS


def test_create_passes_dns_and_keeps_egress():
    """自定义 bridge 网须注入公共 DNS，否则 127.0.0.11 可能无法解析公网。"""
    from app.core.agent_runner import AGENT_RUNNER_DNS, AgentRunnerManager, AgentRunnerSpec

    fake_container = MagicMock()
    fake_container.id = "cid-dns"

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._client.containers.create.return_value = fake_container
    mgr._active_ids = set()

    with patch.object(mgr, "_ensure_image"):
        mgr.create(AgentRunnerSpec(host_workdir="/tmp/x", image="crucible-agent-runner:base"))

    kwargs = mgr._client.containers.create.call_args.kwargs
    assert kwargs["network_disabled"] is False
    assert kwargs["dns"] == AGENT_RUNNER_DNS
    assert kwargs["network"]


def test_create_keeps_default_seccomp_and_drops_linux_capabilities():
    """纯 Linux runner 依赖外层 Docker 边界，不为内层 bwrap 放宽 seccomp。"""
    from app.core.agent_runner import AgentRunnerManager, AgentRunnerSpec

    fake_container = MagicMock()
    fake_container.id = "cid-bwrap"
    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._client.containers.create.return_value = fake_container
    mgr._active_ids = set()

    with patch.object(mgr, "_ensure_image"):
        mgr.create(
            AgentRunnerSpec(
                host_workdir="/tmp/x",
                image="crucible-agent-runner:base",
            )
        )

    kwargs = mgr._client.containers.create.call_args.kwargs
    assert kwargs["user"] == "1000:1000"
    assert kwargs["read_only"] is True
    assert kwargs["cap_drop"] == ["ALL"]
    assert "no-new-privileges" in kwargs["security_opt"]
    assert "seccomp=unconfined" not in kwargs["security_opt"]
    assert "cap_add" not in kwargs


def test_run_with_streaming_injects_runner_auth_token():
    """每次容器执行注入随机 RUNNER_AUTH_TOKEN（server fail-closed 鉴权）。"""
    fake_container = MagicMock()
    fake_container.id = "cid-auth"
    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cid-auth"
    fake_runner.name = "auth-runner"

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._active_ids = set()

    created_specs: list[AgentRunnerSpec] = []

    def spy_create(spec, name=None):
        created_specs.append(spec)
        return fake_runner

    spec = AgentRunnerSpec(host_workdir="/tmp/x", env={"ANTHROPIC_API_KEY": "sk"}, agent_spec={"node_key": "audit"})
    with patch.object(mgr, "create", side_effect=spy_create), \
         patch.object(mgr, "_wait_runner_ready", return_value="http://10.0.0.9:8000"), \
         patch.object(mgr, "_consume_sse", return_value=(0, False, "")):
        mgr.run_with_streaming(spec, lambda e: None)

    token = created_specs[0].env.get("RUNNER_AUTH_TOKEN")
    assert token and len(token) >= 32
    assert created_specs[0].env["ANTHROPIC_API_KEY"] == "sk"
    # 原始 spec 不被就地污染
    assert "RUNNER_AUTH_TOKEN" not in spec.env


def test_create_bind_mounts_skill_dir_readonly():
    from app.core.agent_runner import AgentRunnerManager, AgentRunnerSpec

    fake_container = MagicMock()
    fake_container.id = "cid-skill"

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._client.containers.create.return_value = fake_container
    mgr._active_ids = set()

    with patch.object(mgr, "_ensure_image"):
        mgr.create(AgentRunnerSpec(
            host_workdir="/tmp/work",
            skill_host_dir="/repo/node-skills/triage",
            image="crucible-agent-runner:base",
        ))

    vols = mgr._client.containers.create.call_args.kwargs["volumes"]
    assert vols["/tmp/work"] == {"bind": "/workspace", "mode": "rw"}
    assert vols["/repo/node-skills/triage"] == {"bind": "/node-skill", "mode": "ro"}


def test_ensure_network_rebuilds_internal_for_egress():
    """旧沙箱若把网建成 internal，必须拆掉重建，否则有 DNS 也出不去。"""
    from app.core.agent_runner import AGENT_RUNNER_NETWORK, AgentRunnerManager

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    net = MagicMock()
    net.attrs = {"Internal": True}
    mgr._client.networks.get.return_value = net

    mgr._ensure_network()

    net.remove.assert_called_once()
    mgr._client.networks.create.assert_called_once()
    create_kwargs = mgr._client.networks.create.call_args
    assert create_kwargs.args[0] == AGENT_RUNNER_NETWORK
    assert create_kwargs.kwargs.get("internal") is False


def test_ensure_network_leaves_egress_network_alone():
    from app.core.agent_runner import AgentRunnerManager

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    net = MagicMock()
    net.attrs = {"Internal": False}
    mgr._client.networks.get.return_value = net

    mgr._ensure_network()

    net.remove.assert_not_called()
    mgr._client.networks.create.assert_not_called()


# ── HTTP/SSE 驱动 ──


def test_run_with_streaming_requires_agent_spec():
    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    with pytest.raises(AgentRunnerError, match="agent_spec"):
        mgr.run_with_streaming(AgentRunnerSpec(host_workdir="/tmp/x"), lambda e: None)


def test_run_with_streaming_rejects_network_disabled():
    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    spec = AgentRunnerSpec(
        host_workdir="/tmp/x",
        agent_spec={"node_key": "audit"},
        network_disabled=True,
    )
    with pytest.raises(AgentRunnerError, match="network_disabled"):
        mgr.run_with_streaming(spec, lambda e: None)


def test_run_with_streaming_registers_and_clears_active():
    fake_container = MagicMock()
    fake_container.id = "cid-active"
    fake_container.reload = MagicMock()

    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cid-active"
    fake_runner.name = "active-runner"
    fake_runner.stop_and_remove = MagicMock()

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._active_ids = {"cid-active"}

    spec = AgentRunnerSpec(host_workdir="/tmp/x", agent_spec={"node_key": "audit"})
    with patch.object(mgr, "create", return_value=fake_runner), \
         patch.object(mgr, "_wait_runner_ready", return_value="http://10.0.0.9:8000"), \
         patch.object(
             mgr, "_consume_sse",
             return_value=(0, False, ""),
         ):
        exit_code, summary = mgr.run_with_streaming(spec, lambda e: None)

    assert exit_code == 0
    assert "cid-active" not in mgr._active_ids
    assert summary["transcript"] == ""
    assert summary["oom_killed"] is False
    assert summary["stderr_tail"] == ""


def test_sse_events_flow_to_on_event_and_transcript():
    fake_container = MagicMock()
    fake_container.id = "cid-sse"
    fake_container.reload = MagicMock()
    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cid-sse"
    fake_runner.name = "sse-runner"
    fake_runner.stop_and_remove = MagicMock()

    envelopes = [
        {"event_type": "agent.message", "sequence": 1, "timestamp": 1.0,
         "session_id": "s1", "parent_tool_use_id": None, "payload": {"text": "hi"}},
        {"event_type": "runner.exit", "sequence": 2, "timestamp": 2.0,
         "session_id": "s1", "parent_tool_use_id": None, "payload": {"exit_code": 0}},
    ]

    captured_events: list[dict] = []

    def fake_consume(runner, handle, spec, token, transcript_events, on_event):
        for env in envelopes:
            from app.core.agent_runner import RUNNER_EXIT_EVENT, decode_envelope
            flat = decode_envelope(env)
            transcript_events.append(flat)
            if flat["type"] != RUNNER_EXIT_EVENT:
                on_event(flat)
        return 0, False, ""

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._active_ids = set()

    spec = AgentRunnerSpec(host_workdir="/tmp/x", agent_spec={"node_key": "audit"})
    with patch.object(mgr, "create", return_value=fake_runner), \
         patch.object(mgr, "_wait_runner_ready", return_value="http://10.0.0.9:8000"), \
         patch.object(mgr, "_consume_sse", side_effect=fake_consume):
        exit_code, summary = mgr.run_with_streaming(spec, captured_events.append)

    assert exit_code == 0
    assert [e["type"] for e in captured_events] == ["agent.message"]
    assert "runner.exit" in summary["transcript"]
    # on_ready 收到句柄且 cancel 可用
    assert summary["container_id"] == "cid-sse"


def test_run_with_streaming_propagates_on_ready_handle():
    fake_container = MagicMock()
    fake_container.id = "cid-ready"
    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cid-ready"
    fake_runner.name = "ready-runner"

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._active_ids = set()

    seen: dict = {}

    def on_ready(handle):
        seen["handle"] = handle

    spec = AgentRunnerSpec(host_workdir="/tmp/x", agent_spec={"node_key": "audit"})
    with patch.object(mgr, "create", return_value=fake_runner), \
         patch.object(mgr, "_wait_runner_ready", return_value="http://10.0.0.9:8000"), \
         patch.object(mgr, "_consume_sse", return_value=(0, False, "")):
        mgr.run_with_streaming(spec, lambda e: None, on_ready=on_ready)

    handle = seen["handle"]
    assert handle.container_id == "cid-ready"
    assert handle.base_url == "http://10.0.0.9:8000"


# ── SSE 帧解析 ──


class FakeSSEResponse:
    def __init__(self, lines):
        self._lines = lines

    def iter_lines(self):
        return iter(self._lines)


def test_iter_sse_events_parses_frames_and_skips_bad_json():
    lines = [
        "event: agent_event",
        'data: {"event_type": "agent.message", "payload": {"text": "a"}}',
        "",
        "data: not-json",
        "",
        'data: {"event_type": "runner.exit", "payload": {"exit_code": 1}}',
    ]
    events = list(_iter_sse_events(FakeSSEResponse(lines)))
    assert events[0]["event_type"] == "agent.message"
    assert len(events) == 2, "非法 JSON 帧跳过"


def test_iter_sse_events_handles_trailing_frame_without_blank():
    lines = ['data: {"event_type": "agent.failed"}']
    events = list(_iter_sse_events(FakeSSEResponse(lines)))
    assert events == [{"event_type": "agent.failed"}]


# ── stderr 诊断 ──


def test_failed_container_stderr_in_summary():
    """失败时 _stderr_tail 从 container.logs 取（stderr 优先）。"""
    fake_container = MagicMock()
    fake_container.logs.return_value = b"some error on stderr\nFATAL: bad key\n"
    fake_container.reload = MagicMock()

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    fake_runner = MagicMock()
    fake_runner.container = fake_container
    tail = mgr._stderr_tail(fake_runner)
    assert "FATAL: bad key" in tail


def test_failed_container_combined_logs_when_stderr_stream_empty():
    """stderr-only 可能空，必须回退抓 stdout+stderr。"""
    fake_container = MagicMock()
    fake_container.logs.side_effect = [b"", b"/usr/local/bin/python: ModuleNotFoundError: No module named 'runner'\n"]
    fake_container.reload = MagicMock()

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    fake_runner = MagicMock()
    fake_runner.container = fake_container
    tail = mgr._stderr_tail(fake_runner)
    assert "No module named 'runner'" in tail
