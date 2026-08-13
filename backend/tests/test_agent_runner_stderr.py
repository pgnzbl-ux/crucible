"""验证 run_with_streaming 在容器失败时把 stderr 存入 summary。

回归 bug:容器在 finally 里被 stop_and_remove 删除后,
executor 再去 containers.get 取不到 stderr,导致 error_message
只剩"非0退出"。修复后 summary 应含 stderr_tail。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch

from app.core.agent_runner import AGENT_EXTRA_HOSTS, AgentRunnerManager, AgentRunnerSpec


def test_failed_container_stderr_in_summary():
    """容器非 0 退出时,summary 含 stderr_tail(从 container.logs 取)。"""
    fake_container = MagicMock()
    fake_container.logs.side_effect = [
        # 流式 stdout 消费(run_with_streaming 主循环):返回空,直接 EOF
        iter([]),
        # 失败后抓 stderr 的调用(tail=50, stdout=False, stderr=True)
        b"some error on stderr\nFATAL: bad key\n",
    ]
    fake_container.wait.return_value = {"StatusCode": 1}
    fake_container.attrs = {"State": {"OOMKilled": False}}
    fake_container.id = "cid123"
    fake_container.reload = MagicMock()

    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cid123"
    fake_runner.name = "test-runner"
    fake_runner.stop_and_remove = MagicMock()

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()

    with patch.object(mgr, "create", return_value=fake_runner):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", env={})
        events = []
        exit_code, summary = mgr.run_with_streaming(spec, events.append)

    assert exit_code == 1
    assert "stderr_tail" in summary, "summary 必须含 stderr_tail(失败诊断)"
    assert "FATAL: bad key" in summary["stderr_tail"], "stderr_tail 应含容器 stderr"
    assert summary["container_id"] == "cid123"


def test_success_container_empty_stderr_in_summary():
    """容器成功(exit 0)时,summary 的 stderr_tail 为空串。"""
    fake_container = MagicMock()
    fake_container.logs.side_effect = [iter([])]  # stdout 空,EOF
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_container.attrs = {"State": {"OOMKilled": False}}
    fake_container.id = "cidok"
    fake_container.reload = MagicMock()

    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cidok"
    fake_runner.name = "ok-runner"
    fake_runner.stop_and_remove = MagicMock()

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()

    with patch.object(mgr, "create", return_value=fake_runner):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", env={})
        exit_code, summary = mgr.run_with_streaming(spec, lambda e: None)

    assert exit_code == 0
    assert summary.get("stderr_tail", "MISSING") == ""


def test_extra_hosts_maps_host_docker_internal():
    assert AGENT_EXTRA_HOSTS.get("host.docker.internal") == "host-gateway"


def test_run_with_streaming_registers_and_clears_active():
    fake_container = MagicMock()
    fake_container.logs.side_effect = [iter([])]
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_container.attrs = {"State": {"OOMKilled": False}}
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

    with patch.object(mgr, "create", return_value=fake_runner):
        mgr.run_with_streaming(AgentRunnerSpec(host_workdir="/tmp/x"), lambda e: None)

    assert "cid-active" not in mgr._active_ids


def test_timeout_stops_container():
    fake_container = MagicMock()
    fake_container.logs.side_effect = [iter([])]
    fake_container.wait.return_value = {"StatusCode": 137}
    fake_container.attrs = {"State": {"OOMKilled": False}}
    fake_container.id = "cid-to"
    fake_container.reload = MagicMock()
    fake_container.stop = MagicMock()

    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cid-to"
    fake_runner.name = "to-runner"
    fake_runner.stop_and_remove = MagicMock()

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._active_ids = set()

    class ImmediateTimer:
        def __init__(self, interval, fn, **kwargs):
            self.fn = fn
        def start(self):
            self.fn()
        def cancel(self):
            pass

    with patch.object(mgr, "create", return_value=fake_runner), \
         patch("app.core.agent_runner.threading.Timer", ImmediateTimer):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", timeout_seconds=1)
        exit_code, summary = mgr.run_with_streaming(spec, lambda e: None)

    fake_container.stop.assert_called()
    assert summary.get("timed_out") is True or exit_code == 137
