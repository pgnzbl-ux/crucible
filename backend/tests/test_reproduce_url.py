"""Reproduce 节点：一律注入宿主机 IP:port，禁止 host.docker.internal。"""
import sys
import os
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.reproduce import ReproduceNode


@pytest.mark.asyncio
async def test_reproduce_rewrites_localhost_to_advertise_ip():
    captured = {}

    async def fake_run_ai_node(**kwargs):
        captured.update(kwargs)
        return {"verdict": "confirmed", "reproduced": True}

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={
            "source": {"repo_dirname": "claudecodeui", "workspace_path": "/workspace/claudecodeui"},
            "env_ready": {
                "target_url": "http://localhost:8080",
                "transport_shape": {"protocol": "http"},
                "initial_creds": {"username": "admin", "password": "admin123"},
                "compose_path": ".vuln-env/docker-compose.yml",
                "started_containers": ["app"],
            },
            "audit": {"gate_verdict": "pass", "runtime_dependent": True},
        },
    )
    with patch(
        "app.contexts.agent.nodes.reproduce._resolve_lab_for_reproduce",
        new_callable=AsyncMock,
        side_effect=lambda ctx, env: (env, None),
    ), patch(
        "app.contexts.agent.target_url.host_advertise_ip",
        return_value="10.0.0.8",
    ), patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", fake_run_ai_node):
        await ReproduceNode().execute(ctx)

    inp = captured["input_json"]
    assert inp["target_url"] == "http://10.0.0.8:8080"
    assert "host.docker.internal" not in inp["target_url"]
    assert inp["initial_creds"] == {"username": "admin", "password": "admin123"}
    assert inp["compose_path"] == ".vuln-env/docker-compose.yml"
    assert inp["started_containers"] == ["app"]
    assert inp["source_path"] == "/workspace/claudecodeui"
    assert inp["audit"]["gate_verdict"] == "pass"
    assert inp["audit"]["runtime_dependent"] is True
    assert "on_event" in captured
    assert captured["node_key"] == "reproduce"
    assert "attempt" not in captured


@pytest.mark.asyncio
async def test_reproduce_keeps_published_lan_target_url():
    captured = {}

    async def fake_run_ai_node(**kwargs):
        captured.update(kwargs)
        return {"verdict": "confirmed", "reproduced": True}

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={
            "source": {"repo_dirname": "demo", "workspace_path": "/workspace/demo"},
            "env_ready": {
                "target_url": "http://10.0.0.8:3001",
                "transport_shape": {"protocol": "http"},
                "initial_creds": {"username": "u", "password": "p"},
                "compose_path": ".vuln-env/docker-compose.yml",
                "started_containers": ["web"],
            },
            "audit": {"gate_verdict": "pass"},
        },
    )
    with patch(
        "app.contexts.agent.nodes.reproduce._resolve_lab_for_reproduce",
        new_callable=AsyncMock,
        side_effect=lambda ctx, env: (env, None),
    ), patch("app.contexts.agent.ai_runner.run_ai_node_with_shape_retry", fake_run_ai_node):
        await ReproduceNode().execute(ctx)

    assert captured["input_json"]["target_url"] == "http://10.0.0.8:3001"
    assert captured["input_json"]["initial_creds"] == {"username": "u", "password": "p"}


@pytest.mark.asyncio
async def test_reproduce_degrades_without_env_ready_target_url():
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"env_ready": {}, "audit": {"gate_verdict": "pass", "runtime_dependent": False}},
    )
    output = await ReproduceNode().execute(ctx)
    assert output["verdict"] == "code_reachable"
    assert output["reproduced"] is False
    assert output["degraded_reason"] == "env_unavailable"
