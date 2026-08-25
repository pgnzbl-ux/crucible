"""env_ready 按 Lab 状态复用 / 等待 / 创建。"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.env_ready import EnvReadyNode

SHA = "a" * 40
_VALID_INITIAL_CREDS = {"username": "admin", "password": "secret"}


def _ai_recipe(compose_path: str = ".vuln-env/docker-compose.yml", target_url: str = "http://localhost:3001", **extra) -> dict:
    return {
        "compose_path": compose_path,
        "target_url": target_url,
        "initial_creds": extra.pop("initial_creds", _VALID_INITIAL_CREDS),
        **extra,
    }


def _ctx(tmp_path, **kwargs):
    return NodeContext(
        task_id="t2",
        run_id="r1",
        host_workdir=str(tmp_path),
        source_path=str(tmp_path),
        vulnerability_description="d",
        project_address="https://github.com/a/b",
        project_ref=None,
        previous_outputs={
            "source": {"commit_sha": SHA, "repo_dirname": "b"},
            "profile": {"is_web": True},
        },
        project_id="p1",
        owner_id="u1",
        db_session=_dummy_session(),
        **kwargs,
    )


def _dummy_session():
    """可 await 的哑会话：排障环/探活的取消检查会发 SELECT，结果回非 cancelled。"""
    sess = MagicMock()
    probe = MagicMock()
    probe.scalar_one_or_none.return_value = "running"
    sess.execute = AsyncMock(return_value=probe)
    return sess


def _prepare_lab_service(mock_cls) -> None:
    mock_cls.return_value.heartbeat_creation = AsyncMock(return_value=True)
    mock_cls.return_value.mark_ready = AsyncMock(return_value=True)
    mock_cls.return_value.mark_failed = AsyncMock(return_value=True)
    mock_cls.return_value.live_task_ids = AsyncMock(return_value=["t2"])


@pytest.fixture(autouse=True)
def _mock_runtime_inspection(tmp_path):
    async def runtime_bindings(_project):
        from app.contexts.agent.nodes.env_ready.ports import parse_compose_port_mappings

        for compose_file in sorted(tmp_path.glob("**/.vuln-env/*.yml")):
            mappings = parse_compose_port_mappings(
                compose_file.read_text(encoding="utf-8")
            )
            if mappings:
                return [
                    {
                        "host_ip": "0.0.0.0",
                        "host_port": host_port,
                        "container_port": container_port,
                        "protocol": "tcp",
                    }
                    for host_port, container_port in mappings
                ]
        return []

    running = [
        {
            "name": "web",
            "state": "running",
            "status": "Up 1 minute (healthy)",
            "ports": "0.0.0.0:3001->3000/tcp",
            "image": "x",
        }
    ]
    with patch(
        "app.contexts.agent.nodes.env_ready.ports.load_runtime_web_bindings",
        side_effect=runtime_bindings,
    ), patch(
        "app.contexts.lab.docker_ops.list_containers",
        new_callable=AsyncMock,
        return_value=running,
    ):
        yield


@pytest.mark.asyncio
async def test_env_ready_backfills_missing_creds_without_compose_up(tmp_path):
    from app.contexts.agent.nodes.env_ready import EnvReadyNode
    from app.contexts.agent.nodes.base import NodeContext

    lab = SimpleNamespace(
        lab_id="lab1", role="reuse", status="ready", reused=True,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001", compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    ctx = NodeContext(
        task_id="t2", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="https://github.com/a/b", project_ref=None,
        previous_outputs={"source": {"commit_sha": "a"*40, "repo_dirname": "b"},
                          "profile": {"is_web": True}},
        project_id="p1", owner_id="u1", db_session=_dummy_session(),
    )
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up:
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.touch = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        ai.return_value = {
            "target_url": lab.target_url,
            "compose_path": lab.compose_path,
            "initial_creds": {"username": "admin", "password": "admin123"},
        }
        out = await EnvReadyNode().execute(ctx)
    assert out["target_url"] == "http://10.0.0.8:3001"
    assert out["initial_creds"] == {"username": "admin", "password": "admin123"}
    assert out["reused"] is True
    ai.assert_awaited_once()
    assert ai.await_args.kwargs["credential_lookup_only"] is True
    LS.return_value.mark_ready.assert_awaited_once()
    up.assert_not_awaited()


@pytest.mark.asyncio
async def test_env_ready_reuses_existing_creds_without_ai_or_compose_up(tmp_path):
    lab = SimpleNamespace(
        lab_id="lab1", role="reuse", status="ready", reused=True,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001", compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={"auth_required": False, "note": "公开入口"},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up:
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        out = await EnvReadyNode().execute(ctx)

    assert out["initial_creds"] == {"auth_required": False, "note": "公开入口"}
    ai.assert_not_awaited()
    up.assert_not_awaited()


@pytest.mark.asyncio
async def test_env_ready_waits_then_reuses(tmp_path):
    wait = SimpleNamespace(
        lab_id="lab1", role="wait", status="creating", reused=False,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=None,
        transport_shape={}, initial_creds={},
    )
    ready = SimpleNamespace(
        lab_id="lab1", role="reuse", status="ready", reused=True,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001", compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={"note": "需自行注册"},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.asyncio.sleep", new_callable=AsyncMock) as sleep:
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(side_effect=[wait, ready])
        LS.return_value.touch = AsyncMock()
        out = await EnvReadyNode().execute(ctx)
    assert out["target_url"] == "http://10.0.0.8:3001"
    assert out["reused"] is True
    sleep.assert_awaited()
    ai.assert_not_awaited()
    up.assert_not_awaited()


@pytest.mark.asyncio
async def test_env_ready_create_compose_up_uses_lab_id(tmp_path):
    """就地执行：compose 留在 {host_workdir}/{repo}/.vuln-env，以 lab_id 项目名隔离。"""
    repo = tmp_path / "b" / ".vuln-env"
    repo.mkdir(parents=True)
    (repo / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab-create", role="create", status="creating", reused=False,
        workdir=str(tmp_path / "lab"), compose_project="crucible-lab-lab-create",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=None)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        ai.return_value = _ai_recipe()
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")
        out = await EnvReadyNode().execute(ctx)
    assert up.await_args.kwargs["lab_id"] == "lab-create"
    assert up.await_args.args[1] == str(tmp_path)
    repo_dirname = (
        up.await_args.args[2]
        if len(up.await_args.args) > 2
        else up.await_args.kwargs.get("repo_dirname")
    )
    assert repo_dirname == "b"
    assert (tmp_path / "b" / ".vuln-env" / "docker-compose.yml").is_file()
    assert out["target_url"] == "http://10.0.0.8:3001"
    LS.return_value.mark_ready.assert_awaited()
    ai.assert_awaited()


@pytest.mark.asyncio
async def test_env_ready_upload_failure_cleans_started_compose(tmp_path):
    repo = tmp_path / "b" / ".vuln-env"
    repo.mkdir(parents=True)
    (repo / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab-create", role="create", status="creating", reused=False,
        workdir=str(tmp_path / "lab"), compose_project="crucible-lab-lab-create",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_down", new_callable=AsyncMock) as down, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=None)
        LS.return_value.upload_recipe = AsyncMock(side_effect=RuntimeError("minio down"))
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        ai.return_value = _ai_recipe()
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")

        out = await EnvReadyNode().execute(ctx)
        assert out["ok"] is False
        assert "minio down" in (out.get("error") or "")

    down.assert_awaited_once_with(
        str(tmp_path),
        ".vuln-env/docker-compose.yml",
        "b",
        lab_id="lab-create",
    )
    LS.return_value.mark_failed.assert_awaited()


@pytest.mark.asyncio
async def test_env_ready_create_strips_workspace_compose_path(tmp_path):
    """AI 给出 /workspace/<repo>/... 时，就地执行仍用仓库内相对配方路径。"""
    repo = tmp_path / "b" / ".vuln-env"
    repo.mkdir(parents=True)
    (repo / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab-create", role="create", status="creating", reused=False,
        workdir=str(tmp_path / "lab"), compose_project="crucible-lab-lab-create",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=None)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        ai.return_value = _ai_recipe(
            compose_path="/workspace/b/.vuln-env/docker-compose.yml",
        )
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")
        await EnvReadyNode().execute(ctx)
    assert up.await_args.args[0] == ".vuln-env/docker-compose.yml"
    assert up.await_args.args[1] == str(tmp_path)
    repo_dirname = (
        up.await_args.args[2]
        if len(up.await_args.args) > 2
        else up.await_args.kwargs.get("repo_dirname")
    )
    assert repo_dirname == "b"


@pytest.mark.asyncio
async def test_env_ready_start_stopped_lab_without_ai(tmp_path):
    lab = SimpleNamespace(
        lab_id="lab1", role="start", status="stopped", reused=True,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001", compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={"note": "需自行注册"},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.lab.docker_ops.compose_start", new_callable=AsyncMock) as start, \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up:
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        start.return_value = True
        out = await EnvReadyNode().execute(ctx)
    start.assert_awaited_once_with("crucible-lab-lab1")
    LS.return_value.mark_ready.assert_awaited()
    assert out["target_url"] == "http://10.0.0.8:3001"
    assert out["reused"] is True
    ai.assert_not_awaited()
    up.assert_not_awaited()


@pytest.mark.asyncio
async def test_env_ready_start_gone_runtime_falls_back_to_create(tmp_path):
    repo = tmp_path / "b" / ".vuln-env"
    repo.mkdir(parents=True)
    (repo / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    lab = SimpleNamespace(
        lab_id="lab1", role="start", status="stopped", reused=True,
        workdir=str(lab_dir), compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001", compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.lab.docker_ops.compose_start", new_callable=AsyncMock) as start, \
         patch("app.contexts.lab.docker_ops.list_containers", new_callable=AsyncMock, return_value=[]) as listed, \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=None)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.reclaim_gone_runtime = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        start.return_value = False
        ai.return_value = _ai_recipe()
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")
        out = await EnvReadyNode().execute(ctx)
    start.assert_awaited_once_with("crucible-lab-lab1")
    listed.assert_awaited_with("crucible-lab-lab1")
    assert listed.await_count >= 1
    LS.return_value.reclaim_gone_runtime.assert_awaited_once_with("lab1", "t2")
    LS.return_value.mark_failed.assert_not_awaited()
    ai.assert_awaited()
    up.assert_awaited()
    assert out["target_url"] == "http://10.0.0.8:3001"


@pytest.mark.asyncio
async def test_mock_sdk_skips_lab_acquire(tmp_path):
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS:
        gs.return_value.claude_agent_sdk_enabled = False
        out = await EnvReadyNode().execute(ctx)
    LS.assert_not_called()
    assert out["started_containers"] == ["mock-app"]
    assert out["initial_creds"]["note"]
    assert "target_url" in out


@pytest.mark.asyncio
async def test_reproduce_touches_lab_when_lab_id_present():
    from app.contexts.agent.nodes.reproduce import ReproduceNode

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={
            "source": {"repo_dirname": "b", "workspace_path": "/workspace/b"},
            "env_ready": {"target_url": "http://10.0.0.8:3001"},
            "audit": {"gate_verdict": "pass"},
        },
        lab_id="lab1",
        db_session=_dummy_session(),
    )
    with patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", new_callable=AsyncMock) as ai:
        LS.return_value.touch = AsyncMock()
        LS.return_value.align_runtime_status = AsyncMock()
        ai.return_value = {"verdict": "confirmed"}
        await ReproduceNode().execute(ctx)
    LS.return_value.touch.assert_awaited_once_with("lab1")
    LS.return_value.align_runtime_status.assert_awaited_once_with("lab1")
    LS.return_value.ensure_running.assert_not_called()


@pytest.mark.asyncio
async def test_create_recipe_hit_backfills_creds_without_rebuilding(tmp_path):
    repo_dir = tmp_path / "b"
    (repo_dir / ".vuln-env").mkdir(parents=True)
    (repo_dir / ".vuln-env" / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab1", role="create", status="creating", reused=False,
        workdir=str(tmp_path / "lab"), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    hit = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {},
        "started_containers": ["web"],
    }
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=hit)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        ai.return_value = {
            "target_url": "http://10.0.0.8:3001",
            "compose_path": ".vuln-env/docker-compose.yml",
            "initial_creds": {"username": "admin", "password": "admin123"},
        }
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")
        out = await EnvReadyNode().execute(ctx)
    ai.assert_awaited_once()
    assert ai.await_args.kwargs["credential_lookup_only"] is True
    up.assert_awaited_once()
    assert up.await_args.args[1] == str(tmp_path)
    assert up.await_args.args[2] == "b"
    LS.return_value.download_recipe.await_args.kwargs["dest_workdir"] == str(repo_dir)
    assert out["reused"] is True
    assert out["target_url"] == "http://10.0.0.8:3001"
    assert out["initial_creds"] == {"username": "admin", "password": "admin123"}
    LS.return_value.upload_recipe.assert_awaited()
    LS.return_value.upload_recipe.await_args.kwargs["lab_workdir"] == str(repo_dir)


@pytest.mark.asyncio
async def test_create_recipe_uses_live_container_names_not_ai_guess(tmp_path):
    repo_dir = tmp_path / "b"
    (repo_dir / ".vuln-env").mkdir(parents=True)
    (repo_dir / ".vuln-env" / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab1", role="create", status="creating", reused=False,
        workdir=str(tmp_path / "lab"), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    hit = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {"username": "admin", "password": "admin123"},
        "started_containers": ["ai-guessed"],
    }
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch(
             "app.contexts.lab.docker_ops.list_containers",
             new_callable=AsyncMock,
             return_value=[{"name": "crucible-lab-lab1-web-1", "status": "Up"}],
         ), \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=hit)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")
        out = await EnvReadyNode().execute(ctx)
    ai.assert_not_awaited()
    assert out["started_containers"] == ["crucible-lab-lab1-web-1"]


@pytest.mark.asyncio
async def test_create_recipe_hit_up_fail_goes_ai_once(tmp_path):
    repo = tmp_path / "b" / ".vuln-env"
    repo.mkdir(parents=True)
    (repo / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab_dir = tmp_path / "lab"
    (lab_dir / ".vuln-env").mkdir(parents=True)
    (lab_dir / ".vuln-env" / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab1", role="create", status="creating", reused=False,
        workdir=str(lab_dir), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    hit = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {},
        "started_containers": ["web"],
    }
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_down", new_callable=AsyncMock), \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()), \
         patch("app.contexts.agent.nodes.env_ready.compose_host.collect_compose_logs", new_callable=AsyncMock, return_value="boom"):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=hit)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        up.side_effect = [(False, "build failed"), (True, "")]
        hc.return_value = (True, 3001, "http")
        ai.return_value = _ai_recipe()
        await EnvReadyNode().execute(ctx)
    assert up.await_count == 2
    assert ai.await_count == 1
    assert ai.await_args.args[1] == 1
    assert "build failed" in (ai.await_args.args[2] or "")


@pytest.mark.asyncio
async def test_create_recipe_hit_docker_unavailable_marks_failed_with_daemon_error(tmp_path):
    repo_dir = tmp_path / "b"
    (repo_dir / ".vuln-env").mkdir(parents=True)
    (repo_dir / ".vuln-env" / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab1", role="create", status="creating", reused=False,
        workdir=str(tmp_path / "lab"), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    hit = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {},
        "started_containers": ["web"],
    }
    daemon_err = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=True), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=hit)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        up.return_value = (False, daemon_err)
        out = await EnvReadyNode().execute(ctx)
        assert out["ok"] is False
        assert "docker_unavailable" in (out.get("error") or "")
    ai.assert_not_awaited()
    LS.return_value.mark_failed.assert_awaited()
    reason = LS.return_value.mark_failed.await_args.args[1]
    assert "Cannot connect to the Docker daemon" in reason
    assert reason != "unknown"


@pytest.mark.asyncio
async def test_reuse_dead_lab_degrades_to_rebuild_without_ai(tmp_path):
    """快探失败 → mark_failed + reclaim + 缓存配方重建，不烧 AI。"""
    lab = SimpleNamespace(
        lab_id="lab1", role="reuse", status="ready", reused=True,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001", compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={"note": "x"},
    )
    ctx = _ctx(tmp_path)
    vuln_env = tmp_path / ".vuln-env"
    vuln_env.mkdir(exist_ok=True)
    (vuln_env / "docker-compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '3001:3000'\n", encoding="utf-8"
    )
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive", new_callable=AsyncMock, return_value=False), \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=None)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        LS.return_value.reclaim_gone_runtime = AsyncMock()
        ai.return_value = _ai_recipe()
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")
        out = await EnvReadyNode().execute(ctx)
    LS.return_value.mark_failed.assert_awaited_once()
    LS.return_value.reclaim_gone_runtime.assert_awaited_once()
    # 重建路径走缓存 miss → AI 一轮
    ai.assert_awaited_once()
    assert out["target_url"] == "http://10.0.0.8:3001"


@pytest.mark.asyncio
async def test_reuse_dead_shared_lab_refuses_concurrent_rebuild(tmp_path):
    lab = SimpleNamespace(
        lab_id="lab1",
        role="reuse",
        status="ready",
        reused=True,
        workdir=str(tmp_path),
        compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={"note": "x"},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, patch(
        "app.contexts.lab.service.LabService"
    ) as lab_service, patch(
        "app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn",
        new_callable=AsyncMock,
    ) as ai:
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(lab_service)
        lab_service.return_value.acquire = AsyncMock(return_value=lab)
        lab_service.return_value.live_task_ids = AsyncMock(return_value=["t1", "t2"])

        out = await EnvReadyNode().execute(ctx)
        assert out["ok"] is False
        assert "仍被其他任务使用" in (out.get("error") or "")

    lab_service.return_value.mark_failed.assert_not_awaited()
    ai.assert_not_awaited()


@pytest.mark.asyncio
async def test_reuse_dead_lab_rechecks_users_after_failed_cas(tmp_path):
    lab = SimpleNamespace(
        lab_id="lab1",
        role="reuse",
        status="ready",
        reused=True,
        workdir=str(tmp_path),
        compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001",
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={"note": "x"},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, patch(
        "app.contexts.lab.service.LabService"
    ) as lab_service, patch(
        "app.contexts.agent.nodes.env_ready.reuse._reused_lab_alive",
        new_callable=AsyncMock,
        return_value=False,
    ):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(lab_service)
        lab_service.return_value.acquire = AsyncMock(return_value=lab)
        lab_service.return_value.live_task_ids = AsyncMock(
            side_effect=[["t2"], ["t1", "t2"]]
        )
        lab_service.return_value.reclaim_gone_runtime = AsyncMock()

        out = await EnvReadyNode().execute(ctx)
        assert out["ok"] is False
        assert "出现新的使用任务" in (out.get("error") or "")

    lab_service.return_value.mark_failed.assert_awaited_once()
    lab_service.return_value.mark_ready.assert_awaited_once()
    lab_service.return_value.reclaim_gone_runtime.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_creator_losing_lease_after_up_does_not_down_shared_lab(tmp_path):
    lab = SimpleNamespace(
        lab_id="lab1",
        role="create",
        status="creating",
        reused=False,
        workdir=str(tmp_path),
        compose_project="crucible-lab-lab1",
        target_url=None,
        compose_path=None,
        transport_shape={},
        initial_creds={},
    )
    repo_env = tmp_path / "b" / ".vuln-env"
    repo_env.mkdir(parents=True)
    (repo_env / "docker-compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '3001:3000'\n",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, patch(
        "app.contexts.lab.service.LabService"
    ) as lab_service, patch(
        "app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn",
        new_callable=AsyncMock,
    ) as ai, patch(
        "app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up",
        new_callable=AsyncMock,
        return_value=(True, ""),
    ), patch(
        "app.contexts.agent.nodes.env_ready.compose_host.docker_compose_down",
        new_callable=AsyncMock,
    ) as down, patch(
        "app.contexts.agent.nodes.env_ready.health.health_check",
        new_callable=AsyncMock,
    ) as health_check:
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(lab_service)
        lab_service.return_value.acquire = AsyncMock(return_value=lab)
        lab_service.return_value.download_recipe = AsyncMock(return_value=None)
        lab_service.return_value.heartbeat_creation = AsyncMock(
            side_effect=[True, True, True, False]
        )
        ai.return_value = _ai_recipe()

        out = await EnvReadyNode().execute(ctx)
        assert out["ok"] is False
        assert "创建权已转移" in (out.get("error") or "")

    down.assert_not_awaited()
    health_check.assert_not_awaited()


@pytest.mark.asyncio
async def test_cached_recipe_cred_lookup_failure_tears_down_compose(tmp_path):
    """缓存路径凭据补查失败必须 down 刚 up 的 compose 再抛错。"""
    lab = SimpleNamespace(
        lab_id="lab1", role="create", status="creating", reused=False,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=None,
        transport_shape={}, initial_creds={},
    )
    hit = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {},
        "started_containers": [],
    }
    repo_env = tmp_path / "b" / ".vuln-env"
    repo_env.mkdir(parents=True)
    (repo_env / "docker-compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '3001:3000'\n", encoding="utf-8"
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_down", new_callable=AsyncMock) as down, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=hit)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        # 探活通过后凭据补查炸掉
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")
        ai.side_effect = RuntimeError("AI 补查凭据失败")
        out = await EnvReadyNode().execute(ctx)
        assert out["ok"] is False
        assert "AI 补查凭据失败" in (out.get("error") or "")
    down.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_recipe_failure_emits_warning_event(tmp_path):
    """upload_recipe 失败返回 False → 事件流提示未缓存，但不连坐失败。"""
    lab = SimpleNamespace(
        lab_id="lab1", role="create", status="creating", reused=False,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=None,
        transport_shape={}, initial_creds={},
    )
    (tmp_path / ".vuln-env").mkdir()
    (tmp_path / ".vuln-env" / "docker-compose.yml").write_text(
        "services:\n  web:\n    ports:\n      - '3001:3000'\n", encoding="utf-8"
    )
    ctx = _ctx(tmp_path)
    events: list[dict] = []
    ctx.on_event = events.append
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.ai_recipe.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.compose_host.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        _prepare_lab_service(LS)
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=None)
        LS.return_value.upload_recipe = AsyncMock(return_value=False)
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        ai.return_value = _ai_recipe()
        up.return_value = (True, "")
        hc.return_value = (True, 3001, "http")
        out = await EnvReadyNode().execute(ctx)
    assert out["target_url"] == "http://10.0.0.8:3001"
    warned = [e for e in events if "配方缓存上传失败" in str(e.get("message", ""))]
    assert warned, "upload 失败必须发出警告事件"
