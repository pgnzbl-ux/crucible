"""runner.server HTTP/SSE 契约 + transcript 归档链路。"""

import asyncio
import json
import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

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


import runner.server as runner_server  # noqa: E402
from runner.schemas import RUNNER_EXIT_EVENT, AgentSpec, decode_envelope, encode_envelope  # noqa: E402
from runner.transcript import TranscriptWriter  # noqa: E402

from app.contexts.agent.transcript_archival import archive_node_transcript, get_node_transcript  # noqa: E402
from app.shared.object_store import set_object_store_for_tests  # noqa: E402


def test_agent_spec_schema_and_defaults():
    spec = AgentSpec(
        task_id="t1",
        run_id="r1",
        node_key="triage_batch",
        node_payload={"families": [{"group_id": "g1"}]},
        submit_schema={"type": "object"},
        skill_path="/node-skill/SKILL.md",
        allowed_tools_extra=["Task"],
    )
    assert spec.protocol_version == "3.0"
    assert spec.max_turns == 480
    assert spec.workspace_root == "/workspace"
    assert spec.allowed_tools_extra == ["Task"]
    # 凭据不经 HTTP body 下发（只走容器 env），ABI 中不得出现 secret 字段
    assert "secret" not in AgentSpec.model_fields
    assert not hasattr(spec, "llm_provider")
    assert not hasattr(spec, "task_credentials")


def test_envelope_roundtrip_matches_legacy_flat_events():
    flat = {
        "type": "tool.call.started",
        "tool": "Bash",
        "input": {"command": "ls"},
        "tool_use_id": "t1",
        "parent_tool_use_id": None,
        "session_id": "s1",
        "sequence": 7,
        "timestamp": 123.0,
    }
    envelope = encode_envelope(flat)
    assert envelope["event_type"] == "tool.call.started"
    assert envelope["payload"]["tool"] == "Bash"
    assert "type" not in envelope["payload"]
    decoded = decode_envelope(envelope)
    assert decoded == flat


def test_server_health_and_auth_fail_closed(monkeypatch):
    monkeypatch.delenv("RUNNER_AUTH_TOKEN", raising=False)
    client = TestClient(runner_server.app)

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert "sdk_version" not in data  # 不向沙箱网络泄露版本细节

    # 未配置 token：fail-closed 503；配置后：无/错 token 401
    resp = client.post("/v1/execute", json={"node_key": "audit"})
    assert resp.status_code == 503

    monkeypatch.setenv("RUNNER_AUTH_TOKEN", "tok-abc")
    resp = client.post("/v1/execute", json={"node_key": "audit"})
    assert resp.status_code == 401
    resp = client.post(
        "/v1/execute",
        json={"node_key": "audit"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401
    resp = client.post("/v1/cancel", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_execute_streams_envelopes_and_terminal_exit(monkeypatch, tmp_path):
    """SSE 全链路：扁平事件信封化逐帧下发，runner.exit 终帧收尾并清理凭据 env。"""
    monkeypatch.setenv("RUNNER_AUTH_TOKEN", "tok-ok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    async def fake_run_spec(spec, cancel_event=None):
        yield {"type": "agent.message", "text": "hi", "session_id": "s1"}
        yield {"type": RUNNER_EXIT_EVENT, "exit_code": 0, "node_key": spec.node_key}

    monkeypatch.setattr(runner_server, "run_spec", fake_run_spec)

    client = TestClient(runner_server.app)
    with client.stream(
        "POST",
        "/v1/execute",
        json={"node_key": "audit"},
        headers={"Authorization": "Bearer tok-ok"},
    ) as resp:
        assert resp.status_code == 200
        frames = [line[5:].strip() for line in resp.iter_lines() if line.startswith("data:")]
    events = [decode_envelope(json.loads(f)) for f in frames]
    assert [e["type"] for e in events] == ["agent.message", RUNNER_EXIT_EVENT]
    assert events[1]["exit_code"] == 0
    # 执行结束：单任务槽释放 + Provider 凭据从进程 env 清除
    assert runner_server._is_running is False
    assert os.environ.get("ANTHROPIC_API_KEY") is None


def test_cancel_endpoint_requests_cancellation(monkeypatch):
    monkeypatch.setenv("RUNNER_AUTH_TOKEN", "tok-ok")
    client = TestClient(runner_server.app)

    # 空闲时返回 idle
    runner_server._is_running = False
    resp = client.post("/v1/cancel", headers={"Authorization": "Bearer tok-ok"})
    assert resp.json()["status"] == "idle"

    # 运行中：置 cancel_event（软取消）并 cancel 当前流任务（硬取消）
    runner_server._is_running = True
    try:
        loop = asyncio.new_event_loop()
        task = loop.create_task(asyncio.sleep(3600))
        runner_server._current_task = task
        resp = client.post("/v1/cancel", headers={"Authorization": "Bearer tok-ok"})
        assert resp.json()["status"] == "cancelling"
        assert runner_server._cancel_event.is_set()
        loop.run_until_complete(asyncio.sleep(0.01))
        assert task.cancelled() or task.done()
    finally:
        runner_server._is_running = False
        runner_server._current_task = None


def test_provider_env_guard_restores_and_clears(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live")
    monkeypatch.setattr(runner_server, "_PROVIDER_ENV_SNAPSHOT", {"ANTHROPIC_API_KEY": "sk-live"})
    try:
        os.environ.pop("ANTHROPIC_API_KEY")
        runner_server._apply_provider_env()
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-live"
        runner_server._clear_provider_env()
        assert os.environ.get("ANTHROPIC_API_KEY") is None
    finally:
        os.environ["ANTHROPIC_API_KEY"] = "sk-live"


class MemoryStore:
    def __init__(self):
        self.files = {}

    def put(self, kind, owner_id, data, content_type, **parts):
        key = f"{kind}/{owner_id}/{parts.get('task_id')}/{parts.get('run_id')}/{parts.get('node_key')}.jsonl"
        self.files[key] = data
        from app.shared.object_store import ObjectRef
        return ObjectRef(kind=kind, bucket="crucible-task", key=key)

    def get(self, ref):
        return self.files[ref.key]


def test_transcript_archival_with_mock_store():
    store = MemoryStore()
    set_object_store_for_tests(store)
    try:
        sample_transcript = '{"type": "agent.thinking", "text": "analysis"}\n{"type": "agent.completed"}\n'
        ref = archive_node_transcript("t1", "r1", "triage", "u1", sample_transcript)
        assert ref is not None
        assert "t1/r1/triage" in ref.key

        fetched = get_node_transcript("t1", "r1", "triage", "u1")
        assert fetched == sample_transcript
    finally:
        set_object_store_for_tests(None)


def test_direct_mount_transcript_append(tmp_path):
    """映射卷直写 transcript.jsonl：崩溃/OOM 零丢失，宿主机直接读归档。"""
    log_file = tmp_path / "transcript.jsonl"
    writer = TranscriptWriter(str(log_file))

    writer.append({"type": "agent.thinking", "text": "scanning AST", "sequence": 1})
    writer.append({"type": "agent.completed", "status": "success", "sequence": 2})

    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert "scanning AST" in lines[0]
    assert "success" in lines[1]

    store = MemoryStore()
    set_object_store_for_tests(store)
    try:
        content = log_file.read_text(encoding="utf-8")
        ref = archive_node_transcript("t1", "r1", "triage", "u1", content)
        assert ref is not None
        assert "t1/r1/triage" in ref.key
    finally:
        set_object_store_for_tests(None)
