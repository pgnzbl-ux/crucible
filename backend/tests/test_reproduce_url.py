"""Reproduce 节点把 localhost 靶标改写为容器可达地址。"""
import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.reproduce import ReproduceNode


@pytest.mark.asyncio
async def test_reproduce_rewrites_localhost_target():
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
    with patch("app.contexts.agent.ai_runner.run_ai_node", fake_run_ai_node):
        await ReproduceNode().execute(ctx)

    inp = captured["input_json"]
    assert inp["target_url"] == "http://host.docker.internal:8080"
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
async def test_reproduce_fails_without_env_ready_target_url():
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"env_ready": {}, "audit": {"gate_verdict": "pass", "runtime_dependent": False}},
    )
    with pytest.raises(RuntimeError, match="target_url"):
        await ReproduceNode().execute(ctx)
