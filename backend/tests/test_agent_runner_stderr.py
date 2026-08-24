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


def test_runner_has_no_duration_timeout_and_waits_without_deadline():
    """Agent 不按总运行时长停止；容器自行退出或由取消操作主动拆除。"""
    fake_container = MagicMock()
    fake_container.id = "cid-normal"
    fake_container.logs.side_effect = [iter([])]
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_container.attrs = {"State": {"OOMKilled": False}}

    mgr, fake_runner = _make_mgr_with(fake_container, "normal-runner")
    with patch.object(mgr, "create", return_value=fake_runner):
        spec = AgentRunnerSpec(host_workdir="/tmp/x")
        exit_code, summary = mgr.run_with_streaming(spec, lambda e: None)

    assert not hasattr(spec, "timeout_seconds")
    fake_container.wait.assert_called_once_with(timeout=None)
    assert exit_code == 0
    assert summary["timed_out"] is False
