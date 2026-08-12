"""节点 0 源码 + 节点 1 画像 测试(代码节点)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.profile import ProfileNode
from app.contexts.agent.nodes.source import SourceNode


@pytest.mark.asyncio
async def test_source_node_produces_schema():
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="https://github.com/a/b.git", project_ref="main",
    )
    out = await SourceNode().execute(ctx)
    assert out["project_address"] == "https://github.com/a/b.git"
    assert out["project_ref"] == "main"
    assert out["source_path"] == "/tmp/w"


@pytest.mark.asyncio
async def test_profile_node_produces_schema(tmp_path):
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / "package.json").write_text('{"name":"x","dependencies":{"express":"^4"}}')
    (proj / ".env").write_text("PORT=3000\n")

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
    )
    out = await ProfileNode().execute(ctx)
    assert out["is_web"] is True
    assert out["language"] == "nodejs"
    assert out["framework"] == "express"
    assert out["port"] == 3000


def test_node_context_carries_previous_outputs():
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp",
        source_path="/tmp", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"source": {"commit": "abc"}},
    )
    assert ctx.previous_outputs["source"]["commit"] == "abc"
