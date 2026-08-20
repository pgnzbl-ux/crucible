"""验证 run_with_streaming 在容器失败时把 stderr 存入 summary。

回归 bug:容器在 finally 里被 stop_and_remove 删除后,
executor 再去 containers.get 取不到 stderr,导致 error_message
只剩"非0退出"。修复后 summary 应含 stderr_tail。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import MagicMock, patch

import docker
import requests

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


def test_failed_container_combined_logs_when_stderr_stream_empty():
    """stdout=False/stderr=True 可能空，必须回退抓 stdout+stderr。"""
    fake_container = MagicMock()
    fake_container.logs.side_effect = [
        iter([]),
        b"",  # stderr-only 空
        b"/usr/local/bin/python: ModuleNotFoundError: No module named 'runner'\n",
    ]
    fake_container.wait.return_value = {"StatusCode": 1}
    fake_container.attrs = {"State": {"OOMKilled": False}}
    fake_container.id = "cid-empty-stderr"
    fake_container.reload = MagicMock()

    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = "cid-empty-stderr"
    fake_runner.name = "empty-stderr-runner"
    fake_runner.stop_and_remove = MagicMock()

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()

    with patch.object(mgr, "create", return_value=fake_runner):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", env={})
        exit_code, summary = mgr.run_with_streaming(spec, lambda e: None)

    assert exit_code == 1
    assert "No module named 'runner'" in summary["stderr_tail"]


# ── P0#5/6：超时 stop 失败 / wait 无界 / 边界竞态 ──


class _ImmediateTimer:
    def __init__(self, interval, fn, **kwargs):
        self.fn = fn

    def start(self):
        self.fn()

    def cancel(self):
        pass


def _make_mgr_with(fake_container, runner_name="r"):
    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._active_ids = set()
    fake_runner = MagicMock()
    fake_runner.container = fake_container
    fake_runner.id = fake_container.id
    fake_runner.name = runner_name
    fake_runner.stop_and_remove = MagicMock()
    return mgr, fake_runner


def test_timeout_stop_failure_escalates_to_kill_and_surfaces():
    """stop 抛异常必须降级 kill 并把失败信息写进 summary（不能 except:pass 吞掉）。"""
    fake_container = MagicMock()
    fake_container.id = "cid-hang"
    fake_container.logs.side_effect = [iter([])]
    fake_container.wait.return_value = {"StatusCode": 137}
    fake_container.attrs = {"State": {"OOMKilled": False}}
    fake_container.stop.side_effect = docker.errors.APIError("stop refused")
    fake_container.kill = MagicMock()

    mgr, fake_runner = _make_mgr_with(fake_container, "hang-runner")
    with patch.object(mgr, "create", return_value=fake_runner), \
         patch("app.core.agent_runner.threading.Timer", _ImmediateTimer):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", timeout_seconds=1)
        exit_code, summary = mgr.run_with_streaming(spec, lambda e: None)

    fake_container.kill.assert_called_once()
    assert "stop 失败" in summary["stop_failed"]
    assert exit_code == 137


def test_timeout_wait_readtimeout_falls_back_to_137():
    """stop/kill 均未生效时 wait() 兜底超时，不能无界阻塞，按 137 收尾。"""
    fake_container = MagicMock()
    fake_container.id = "cid-stuck"
    fake_container.logs.side_effect = [iter([])]
    fake_container.wait.side_effect = requests.exceptions.ReadTimeout("read timed out")
    fake_container.attrs = {"State": {"OOMKilled": False}}

    mgr, fake_runner = _make_mgr_with(fake_container, "stuck-runner")
    with patch.object(mgr, "create", return_value=fake_runner), \
         patch("app.core.agent_runner.threading.Timer", _ImmediateTimer):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", timeout_seconds=1)
        exit_code, summary = mgr.run_with_streaming(spec, lambda e: None)

    assert exit_code == 137
    assert summary["timed_out"] is True


def test_timeout_grace_period_passed_to_wait():
    """超时后 wait 必须带兜底 timeout（非 None）；正常路径不传。"""
    fake_container = MagicMock()
    fake_container.id = "cid-grace"
    fake_container.logs.side_effect = [iter([])]
    fake_container.wait.return_value = {"StatusCode": 137}
    fake_container.attrs = {"State": {"OOMKilled": False}}

    mgr, fake_runner = _make_mgr_with(fake_container, "grace-runner")
    with patch.object(mgr, "create", return_value=fake_runner), \
         patch("app.core.agent_runner.threading.Timer", _ImmediateTimer):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", timeout_seconds=1)
        mgr.run_with_streaming(spec, lambda e: None)

    fake_container.wait.assert_called_once_with(timeout=30)


def test_timedout_but_clean_exit_keeps_artifacts():
    """超时瞬间 agent 已写完 output（exit 0）：保留真实退出码，不判失败。"""
    fake_container = MagicMock()
    fake_container.id = "cid-race"
    fake_container.logs.side_effect = [iter([])]
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_container.attrs = {"State": {"OOMKilled": False}}

    mgr, fake_runner = _make_mgr_with(fake_container, "race-runner")
    with patch.object(mgr, "create", return_value=fake_runner), \
         patch("app.core.agent_runner.threading.Timer", _ImmediateTimer):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", timeout_seconds=1)
        exit_code, summary = mgr.run_with_streaming(spec, lambda e: None)

    assert exit_code == 0
    assert summary["timed_out"] is True


def test_normal_run_wait_without_grace():
    """正常完成（未超时）wait() 不带 timeout——容器自己退出，无需兜底。"""
    fake_container = MagicMock()
    fake_container.id = "cid-normal"
    fake_container.logs.side_effect = [iter([])]
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_container.attrs = {"State": {"OOMKilled": False}}

    mgr, fake_runner = _make_mgr_with(fake_container, "normal-runner")
    with patch.object(mgr, "create", return_value=fake_runner):
        spec = AgentRunnerSpec(host_workdir="/tmp/x", timeout_seconds=60)
        exit_code, _ = mgr.run_with_streaming(spec, lambda e: None)

    fake_container.wait.assert_called_once_with(timeout=None)
    assert exit_code == 0
