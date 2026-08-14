"""节点 2 靶场就绪 — 排障循环测试(mock docker + AI)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from types import SimpleNamespace

from app.contexts.agent.nodes.base import NodeContext


def _write_compose(tmp_path, filename="docker-compose.yml", mapping="8000:8000", repo="project"):
    d = tmp_path / repo / ".vuln-env"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(
        f"services:\n  web:\n    image: x\n    ports:\n      - \"{mapping}\"\n",
        encoding="utf-8",
    )


def _exec_ctx(tmp_path, profile=None):
    return NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={
            "source": {"commit_sha": "a" * 40, "repo_dirname": "project"},
            "profile": profile or {"is_web": True},
        },
        project_id="p1", owner_id="u1", db_session=object(),
    )


@pytest.fixture(autouse=True)
def _enable_sdk():
    """测试排障循环需走真实路径,强制 SDK 启用(否则 execute 走 mock 直接返回)。"""
    with patch("app.core.config.get_settings") as gs:
        s = MagicMock()
        s.claude_agent_sdk_enabled = True
        s.agent_runner_timeout_seconds = 1800
        gs.return_value = s
        yield


@pytest.fixture(autouse=True)
def _mock_lab_acquire(tmp_path):
    """execute 会 acquire；单测不进真实 DB，一律当作本任务创建。"""
    lab = SimpleNamespace(
        lab_id="lab-test",
        role="create",
        status="creating",
        reused=False,
        workdir=str(tmp_path),
        compose_project="crucible-lab-lab-test",
        target_url=None,
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={},
        initial_creds={},
    )
    with patch("app.contexts.lab.service.LabService") as LS:
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.mark_ready = AsyncMock()
        LS.return_value.mark_failed = AsyncMock()
        LS.return_value.touch = AsyncMock()
        yield lab


@pytest.fixture(autouse=True)
def _free_docker_ports():
    """默认假定宿主机没有被其他容器占用的映射口，避免测试机 docker ps 干扰。"""
    with patch(
        "app.contexts.agent.nodes.env_ready.list_docker_occupied_host_ports",
        return_value=set(),
        create=True,
    ):
        yield


@pytest.mark.asyncio
async def test_env_ready_first_attempt_success(tmp_path):
    """AI 首轮产 compose,worker 起来健康检查通过 → 成功。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = _exec_ctx(tmp_path, {"is_web": True, "language": "python", "port": 8000})
    _write_compose(tmp_path)

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(mod, "host_advertise_ip", return_value="192.168.1.8"):
        mock_ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:8000",
        }
        mock_up.return_value = (True, "")
        mock_hc.return_value = (True, 8000)

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert out["target_url"] == "http://192.168.1.8:8000"
    assert mock_ai.call_count == 1
    mock_hc.assert_awaited()


@pytest.mark.asyncio
async def test_env_ready_retry_until_success(tmp_path):
    """前 2 轮起容器失败,第 3 轮 AI 改对 → 成功。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})
    _write_compose(tmp_path, filename="1.yml")
    _write_compose(tmp_path, filename="2.yml")
    _write_compose(tmp_path, filename="3.yml")

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(mod, "docker_compose_down", new_callable=AsyncMock), \
         patch.object(mod, "host_advertise_ip", return_value="192.168.1.8"):
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
        mock_hc.return_value = (True, 8000)

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert mock_ai.call_count == 3
    assert out["target_url"] == "http://192.168.1.8:8000"


@pytest.mark.asyncio
async def test_env_ready_5_fails_then_node_fails(tmp_path):
    """5 轮全失败 → 节点 failed(分支出口 C)。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(mod, "docker_compose_down", new_callable=AsyncMock):
        mock_ai.return_value = {"compose_path": ".vuln-env/x.yml", "target_url": "http://localhost:8000"}
        mock_up.return_value = (False, "persistent fail")
        mock_logs.return_value = ""

        node = mod.EnvReadyNode()
        with pytest.raises(RuntimeError, match="5"):
            await node.execute(ctx)

    assert mock_ai.call_count == 5


@pytest.mark.asyncio
async def test_env_ready_health_fail_retries_ai_with_logs(tmp_path):
    """compose 起来但探活失败 → 收日志回喂 AI，下一轮成功则返回局域网地址。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 3001})
    _write_compose(tmp_path, mapping="3001:3001")

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(mod, "docker_compose_down", new_callable=AsyncMock) as mock_down, \
         patch.object(mod, "host_advertise_ip", return_value="192.168.1.8"):
        mock_ai.side_effect = [
            {"compose_path": ".vuln-env/docker-compose.yml", "target_url": "http://localhost:3001"},
            {"compose_path": ".vuln-env/docker-compose.yml", "target_url": "http://127.0.0.1:3001"},
        ]
        mock_up.return_value = (True, "")
        mock_logs.return_value = "app exited 1"
        mock_hc.side_effect = [(False, None), (True, 3001)]

        out = await mod.EnvReadyNode().execute(ctx)

    assert mock_ai.call_count == 2
    assert mock_down.await_count == 1
    assert "健康检查不过" in mock_ai.call_args_list[1].args[2]
    assert "app exited 1" in mock_ai.call_args_list[1].args[2]
    assert out["target_url"] == "http://192.168.1.8:3001"


def test_parse_compose_host_ports_short_and_long():
    from app.contexts.agent.nodes.env_ready import parse_compose_port_mappings, web_host_ports

    text = """
services:
  web:
    ports:
      - "3001:3000"
      - 8080:80
  db:
    ports:
      - "5432:5432"
"""
    maps = parse_compose_port_mappings(text)
    assert (3001, 3000) in maps
    assert (8080, 80) in maps
    assert (5432, 5432) in maps
    assert web_host_ports(maps) == [3001, 8080]

    long_form = """
services:
  web:
    ports:
      - target: 3000
        published: 3001
"""
    assert parse_compose_port_mappings(long_form) == [(3001, 3000)]


@pytest.mark.asyncio
async def test_health_check_does_not_scan_host_common_ports():
    from app.contexts.agent.nodes import env_ready as mod

    seen: list[str] = []

    def fake_alive(url: str, timeout: float = 5) -> bool:
        seen.append(url)
        return False

    with (
        patch.object(mod, "_http_alive", fake_alive),
        patch.object(mod, "HEALTH_RETRIES", 1),
        patch.object(mod, "HEALTH_RETRY_SECONDS", 0),
    ):
        ok, port = await mod.health_check([3001])
    assert ok is False
    assert port is None
    assert seen == ["http://127.0.0.1:3001"]


@pytest.mark.asyncio
async def test_env_ready_url_uses_mapped_host_port_not_container_port(tmp_path):
    """AI 写了 3001:3000 却把 target_url 写成容器端口 3000 → 对外仍用宿主机映射口。"""
    from app.contexts.agent.nodes import env_ready as mod

    _write_compose(tmp_path, mapping="3001:3000")
    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 3000})
    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(mod, "host_advertise_ip", return_value="10.0.0.8"):
        mock_ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:3000",
        }
        mock_up.return_value = (True, "")
        mock_hc.return_value = (True, 3001)
        out = await mod.EnvReadyNode().execute(ctx)
    assert mock_hc.await_args.args[0] == [3001]
    assert out["target_url"] == "http://10.0.0.8:3001"


@pytest.mark.asyncio
async def test_env_ready_rejects_db_only_port_mapping(tmp_path):
    from app.contexts.agent.nodes import env_ready as mod

    _write_compose(tmp_path, mapping="5432:5432")
    ctx = _exec_ctx(tmp_path, {"is_web": True})
    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "collect_compose_logs", new_callable=AsyncMock, return_value=""), \
         patch.object(mod, "docker_compose_down", new_callable=AsyncMock) as mock_down:
        mock_ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:5432",
        }
        mock_up.return_value = (True, "")
        with pytest.raises(RuntimeError, match="5"):
            await mod.EnvReadyNode().execute(ctx)
    assert mock_up.await_count == 0
    assert mock_down.await_count == 0
    assert "Web 端口" in mock_ai.call_args_list[-1].args[2]


def test_parse_docker_ps_published_ports_and_exclude_own_lab():
    from app.contexts.agent.nodes.env_ready import parse_docker_ps_published_ports

    text = (
        "other-app\t0.0.0.0:3001->3000/tcp, [::]:3001->3000/tcp\n"
        "db\t127.0.0.1:5432->5432/tcp\n"
        "unpublished\t80/tcp\n"
        "crucible-lab-t1\t0.0.0.0:8000->8000/tcp\n"
        "\t0.0.0.0:9000->9000/tcp\n"
    )
    assert parse_docker_ps_published_ports(text) == {3001, 5432, 8000, 9000}
    assert parse_docker_ps_published_ports(text, exclude_project="crucible-lab-t1") == {
        3001, 5432, 9000,
    }


@pytest.mark.asyncio
async def test_run_ai_turn_passes_occupied_host_ports():
    """AI 写配方前就能看到 docker 已占用的宿主端口，避开再映射。"""
    from app.contexts.agent.nodes.env_ready import run_ai_turn

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp",
        source_path="/tmp", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"source": {"repo_dirname": "demo", "workspace_path": "/workspace/demo"}},
    )
    with patch("app.contexts.agent.ai_runner.run_ai_node", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://127.0.0.1:3001",
        }
        await run_ai_turn(ctx, 1, None, occupied_host_ports=[3001, 8080])
    assert mock_run.await_args.kwargs["input_json"]["occupied_host_ports"] == [3001, 8080]


@pytest.mark.asyncio
async def test_env_ready_occupied_host_port_skips_compose_up(tmp_path):
    """配方里的宿主映射口已被其他容器占用 → 不起 compose，回喂 AI 改宿主侧端口。"""
    from app.contexts.agent.nodes import env_ready as mod

    _write_compose(tmp_path, mapping="3001:3000")
    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 3000})
    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(mod, "list_docker_occupied_host_ports", return_value={3001}):
        mock_ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:3001",
        }
        mock_up.return_value = (True, "")
        with pytest.raises(RuntimeError, match="5"):
            await mod.EnvReadyNode().execute(ctx)
    assert mock_up.await_count == 0
    assert mock_hc.await_count == 0
    assert mock_ai.call_count == 5
    assert mock_ai.call_args_list[0].kwargs["occupied_host_ports"] == [3001]
    err = mock_ai.call_args_list[-1].args[2]
    assert "3001" in err
    assert "占用" in err


def test_ai_nodes_import_ok():
    """5 个 AI 节点都能 import。"""
    from app.contexts.agent.nodes.profile import ProfileNode
    from app.contexts.agent.nodes.env_ready import EnvReadyNode
    from app.contexts.agent.nodes.audit import AuditNode
    from app.contexts.agent.nodes.reproduce import ReproduceNode
    from app.contexts.agent.nodes.report import ReportNode

    for cls in (ProfileNode, EnvReadyNode, AuditNode, ReproduceNode, ReportNode):
        instance = cls()
        assert instance.is_ai is True
