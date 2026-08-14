"""env_ready 按 Lab 状态复用 / 等待 / 创建。"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.env_ready import EnvReadyNode

SHA = "a" * 40


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
        db_session=object(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_env_ready_reuses_ready_lab_without_compose_up(tmp_path):
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
        project_id="p1", owner_id="u1", db_session=object(),
    )
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.docker_compose_up", new_callable=AsyncMock) as up:
        gs.return_value.claude_agent_sdk_enabled = True
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.touch = AsyncMock()
        out = await EnvReadyNode().execute(ctx)
    assert out["target_url"] == "http://10.0.0.8:3001"
    assert out["reused"] is True
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
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.asyncio.sleep", new_callable=AsyncMock) as sleep:
        gs.return_value.claude_agent_sdk_enabled = True
        gs.return_value.agent_runner_timeout_seconds = 1800
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
    repo = tmp_path / "b" / ".vuln-env"
    repo.mkdir(parents=True)
    (repo / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    lab = SimpleNamespace(
        lab_id="lab-create", role="create", status="creating", reused=False,
        workdir=str(lab_dir), compose_project="crucible-lab-lab-create",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.nodes.env_ready.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.mark_ready = AsyncMock()
        LS.return_value.mark_failed = AsyncMock()
        ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:3001",
        }
        up.return_value = (True, "")
        hc.return_value = (True, 3001)
        out = await EnvReadyNode().execute(ctx)
    assert up.await_args.kwargs["lab_id"] == "lab-create"
    assert up.await_args.args[1] == str(lab_dir)
    assert (lab_dir / ".vuln-env" / "docker-compose.yml").is_file()
    assert out["target_url"] == "http://10.0.0.8:3001"
    LS.return_value.mark_ready.assert_awaited()
    ai.assert_awaited()


@pytest.mark.asyncio
async def test_env_ready_start_stopped_lab_without_ai(tmp_path):
    lab = SimpleNamespace(
        lab_id="lab1", role="start", status="stopped", reused=True,
        workdir=str(tmp_path), compose_project="crucible-lab-lab1",
        target_url="http://10.0.0.8:3001", compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.lab.docker_ops.compose_start", new_callable=AsyncMock) as start, \
         patch("app.contexts.agent.nodes.env_ready.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.docker_compose_up", new_callable=AsyncMock) as up:
        gs.return_value.claude_agent_sdk_enabled = True
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.mark_ready = AsyncMock()
        LS.return_value.mark_failed = AsyncMock()
        start.return_value = True
        out = await EnvReadyNode().execute(ctx)
    start.assert_awaited_once_with("crucible-lab-lab1")
    LS.return_value.mark_ready.assert_awaited()
    assert out["target_url"] == "http://10.0.0.8:3001"
    assert out["reused"] is True
    ai.assert_not_awaited()
    up.assert_not_awaited()


@pytest.mark.asyncio
async def test_mock_sdk_skips_lab_acquire(tmp_path):
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS:
        gs.return_value.claude_agent_sdk_enabled = False
        out = await EnvReadyNode().execute(ctx)
    LS.assert_not_called()
    assert out["started_containers"] == ["mock-app"]
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
        db_session=object(),
    )
    with patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.ai_runner.run_ai_node", new_callable=AsyncMock) as ai:
        LS.return_value.touch = AsyncMock()
        ai.return_value = {"verdict": "confirmed"}
        await ReproduceNode().execute(ctx)
    LS.return_value.touch.assert_awaited_once_with("lab1")
