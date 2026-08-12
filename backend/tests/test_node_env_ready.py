"""节点 2 靶场就绪 — 排障循环测试(mock docker + AI)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.contexts.agent.nodes.base import NodeContext


@pytest.fixture(autouse=True)
def _enable_sdk():
    """测试排障循环需走真实路径,强制 SDK 启用(否则 execute 走 mock 直接返回)。"""
    with patch("app.core.config.get_settings") as gs:
        s = MagicMock()
        s.claude_agent_sdk_enabled = True
        gs.return_value = s
        yield


@pytest.mark.asyncio
async def test_env_ready_first_attempt_success(tmp_path):
    """AI 首轮产 compose,worker 起来健康检查通过 → 成功。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"profile": {"is_web": True, "language": "python", "port": 8000}},
    )

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc:
        mock_ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:8000",
        }
        mock_up.return_value = (True, "")

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert out["target_url"] == "http://localhost:8000"
    assert mock_ai.call_count == 1


@pytest.mark.asyncio
async def test_env_ready_retry_until_success(tmp_path):
    """前 2 轮起容器失败,第 3 轮 AI 改对 → 成功。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"profile": {"is_web": True, "port": 8000}},
    )

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "collect_compose_logs", new_callable=AsyncMock) as mock_logs:
        mock_ai.side_effect = [
            {"compose_path": ".vuln-env/1.yml", "target_url": "http://localhost:8000"},
            {"compose_path": ".vuln-env/2.yml", "target_url": "http://localhost:8000"},
            {"compose_path": ".vuln-env/3.yml", "target_url": "http://localhost:8000"},
        ]
        mock_up.side_effect = [
            (False, "port in use"),
            (False, "build fail"),
            (True, ""),
        ]
        mock_logs.return_value = ""

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert mock_ai.call_count == 3
    assert out["target_url"] == "http://localhost:8000"


@pytest.mark.asyncio
async def test_env_ready_5_fails_then_node_fails(tmp_path):
    """5 轮全失败 → 节点 failed(分支出口 C)。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"profile": {"is_web": True, "port": 8000}},
    )

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "collect_compose_logs", new_callable=AsyncMock) as mock_logs:
        mock_ai.return_value = {"compose_path": ".vuln-env/x.yml", "target_url": "http://localhost:8000"}
        mock_up.return_value = (False, "persistent fail")
        mock_logs.return_value = ""

        node = mod.EnvReadyNode()
        with pytest.raises(RuntimeError, match="5"):
            await node.execute(ctx)

    assert mock_ai.call_count == 5


def test_ai_nodes_import_ok():
    """4 个 AI 节点都能 import。"""
    from app.contexts.agent.nodes.env_ready import EnvReadyNode
    from app.contexts.agent.nodes.audit import AuditNode
    from app.contexts.agent.nodes.reproduce import ReproduceNode
    from app.contexts.agent.nodes.report import ReportNode

    for cls in (EnvReadyNode, AuditNode, ReproduceNode, ReportNode):
        instance = cls()
        assert instance.is_ai is True
