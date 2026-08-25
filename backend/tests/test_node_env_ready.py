"""节点 2 靶场就绪 — 排障循环测试(mock docker + AI)。"""
import asyncio
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext

_VALID_INITIAL_CREDS = {"username": "admin", "password": "secret"}


def _recipe(compose_path: str, target_url: str, **extra) -> dict:
    return {
        "compose_path": compose_path,
        "target_url": target_url,
        "initial_creds": _VALID_INITIAL_CREDS,
        **extra,
    }


def _write_compose(tmp_path, filename="docker-compose.yml", mapping="8000:8000", repo="project"):
    d = tmp_path / repo / ".vuln-env"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(
        f"services:\n  web:\n    image: x\n    ports:\n      - \"{mapping}\"\n",
        encoding="utf-8",
    )


def _exec_ctx(tmp_path, profile=None):
    # db_session 用可 await 的哑会话：排障环/探活的取消检查会对其发 SELECT，
    # 结果给非 cancelled 状态（"running"）让检查短路通过
    sess = MagicMock()
    probe = MagicMock()
    probe.scalar_one_or_none.return_value = "running"
    sess.execute = AsyncMock(return_value=probe)
    return NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={
            "source": {"commit_sha": "a" * 40, "repo_dirname": "project"},
            "profile": profile or {"is_web": True},
        },
        project_id="p1", owner_id="u1", db_session=sess,
    )


@pytest.fixture(autouse=True)
def _enable_sdk():
    """测试排障循环需走真实路径,强制 SDK 启用(否则 execute 走 mock 直接返回)。"""
    with patch("app.core.config.get_settings") as gs:
        s = MagicMock()
        s.claude_agent_sdk_enabled = True
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
        LS.return_value.download_recipe = AsyncMock(return_value=None)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock(return_value=True)
        LS.return_value.mark_failed = AsyncMock(return_value=True)
        LS.return_value.heartbeat_creation = AsyncMock(return_value=True)
        LS.return_value.live_task_ids = AsyncMock(return_value=["t1"])
        LS.return_value.touch = AsyncMock()
        yield lab


@pytest.fixture(autouse=True)
def _free_docker_ports(tmp_path):
    """默认假定宿主机没有被其他容器占用的映射口，避免测试机 docker ps 干扰。"""
    async def runtime_bindings(_project):
        files = sorted((tmp_path / "project" / ".vuln-env").glob("*.yml"))
        for compose_file in files:
            from app.contexts.agent.nodes.env_ready.ports import (
                parse_compose_port_mappings,
                web_host_ports,
            )

            mappings = parse_compose_port_mappings(
                compose_file.read_text(encoding="utf-8")
            )
            host_ports = web_host_ports(mappings)
            if host_ports:
                return [
                    {
                        "host_ip": "0.0.0.0",
                        "host_port": host_port,
                        "container_port": container_port,
                        "protocol": "tcp",
                    }
                    for host_port, container_port in mappings
                    if host_port in host_ports
                ]
        return []

    with patch(
        "app.contexts.agent.nodes.env_ready.ports.list_docker_occupied_host_ports",
        return_value=set(),
        create=True,
    ), patch(
        "app.contexts.agent.nodes.env_ready.ports.load_runtime_web_bindings",
        side_effect=runtime_bindings,
    ), patch(
        "app.contexts.agent.nodes.env_ready.reuse._live_started_containers",
        new_callable=AsyncMock,
        return_value=["web"],
    ):
        yield


@pytest.mark.asyncio
async def test_env_ready_first_attempt_success(tmp_path):
    """AI 首轮产 compose,worker 起来健康检查通过 → 成功。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    ctx = _exec_ctx(tmp_path, {"is_web": True, "language": "python", "port": 8000})
    _write_compose(tmp_path)

    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(health, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="192.168.1.8"):
        mock_ai.return_value = _recipe(
            ".vuln-env/docker-compose.yml",
            "http://localhost:8000",
        )
        mock_up.return_value = (True, "")
        mock_hc.return_value = (True, 8000, "http")

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert out["target_url"] == "http://192.168.1.8:8000"
    assert out["initial_creds"] == _VALID_INITIAL_CREDS
    assert mock_ai.call_count == 1
    mock_hc.assert_awaited()


@pytest.mark.asyncio
async def test_env_ready_retry_until_success(tmp_path):
    """前 2 轮起容器失败,第 3 轮 AI 改对 → 成功。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})
    _write_compose(tmp_path, filename="1.yml")
    _write_compose(tmp_path, filename="2.yml")
    _write_compose(tmp_path, filename="3.yml")

    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(compose_host, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(health, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(compose_host, "docker_compose_down", new_callable=AsyncMock), \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="192.168.1.8"):
        mock_ai.side_effect = [
            _recipe(".vuln-env/1.yml", "http://localhost:8000"),
            _recipe(".vuln-env/2.yml", "http://localhost:8000"),
            _recipe(".vuln-env/3.yml", "http://localhost:8000"),
        ]
        mock_up.side_effect = [
            (False, "port in use"),
            (False, "build fail"),
            (True, ""),
        ]
        mock_logs.return_value = ""
        mock_hc.return_value = (True, 8000, "http")

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert mock_ai.call_count == 3
    assert mock_ai.call_args_list[1].kwargs["failed_stage"] == "port_conflict"
    assert "port in use" in mock_ai.call_args_list[1].args[2]
    assert mock_ai.call_args_list[2].kwargs["failed_stage"] == "compose_build"
    assert "build fail" in mock_ai.call_args_list[2].args[2]
    assert out["target_url"] == "http://192.168.1.8:8000"


@pytest.mark.asyncio
async def test_env_ready_container_healthcheck_failure_is_labeled_for_next_round(tmp_path):
    """compose --wait 的 unhealthy 必须区别于构建/启动失败并携带诊断。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import ai_recipe, compose_host, health

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})
    _write_compose(tmp_path)

    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(compose_host, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(compose_host, "docker_compose_down", new_callable=AsyncMock), \
         patch.object(health, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="192.168.1.8"):
        mock_ai.side_effect = [
            _recipe(".vuln-env/docker-compose.yml", "http://localhost:8000"),
            _recipe(".vuln-env/docker-compose.yml", "http://localhost:8000"),
        ]
        mock_up.side_effect = [
            (False, "container project-web-1 is unhealthy"),
            (True, ""),
        ]
        mock_logs.return_value = (
            "web healthcheck: exit=1 output=curl: connection refused"
        )
        mock_hc.return_value = (True, 8000, "http")

        await mod.EnvReadyNode().execute(ctx)

    retry = mock_ai.call_args_list[1]
    assert retry.kwargs["failed_stage"] == "container_healthcheck"
    assert "Docker healthcheck 失败" in retry.args[2]
    assert "curl: connection refused" in retry.args[2]


@pytest.mark.asyncio
async def test_env_ready_docker_platform_failure_does_not_consume_ai_retries(tmp_path):
    """Docker daemon/权限故障是平台错误，不能让 AI 连续改五轮配方。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import ai_recipe, compose_host

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})
    _write_compose(tmp_path)

    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(compose_host, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(compose_host, "docker_compose_down", new_callable=AsyncMock) as mock_down:
        mock_ai.return_value = _recipe(
            ".vuln-env/docker-compose.yml", "http://localhost:8000"
        )
        mock_up.return_value = (
            False,
            "Cannot connect to the Docker daemon. Is the docker daemon running?",
        )

        out = await mod.EnvReadyNode().execute(ctx)

    assert out["ok"] is False
    assert "docker_unavailable" in (out.get("error") or "")

    assert mock_ai.await_count == 1
    mock_logs.assert_not_awaited()
    mock_down.assert_not_awaited()


@pytest.mark.asyncio
async def test_env_ready_5_fails_then_degrades_completed(tmp_path):
    """5 轮全失败 → 节点 completed 降级（ok=false），不得杀整任务。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})

    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(compose_host, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(compose_host, "docker_compose_down", new_callable=AsyncMock):
        mock_ai.return_value = _recipe(".vuln-env/x.yml", "http://localhost:8000")
        mock_up.return_value = (False, "persistent fail")
        mock_logs.return_value = ""

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert out["ok"] is False
    assert out["target_url"] is None
    assert mock_ai.call_count == 5


@pytest.mark.asyncio
async def test_env_ready_health_fail_retries_ai_with_logs(tmp_path):
    """compose 起来但探活失败 → 收日志回喂 AI，下一轮成功则返回局域网地址。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 3001})
    _write_compose(tmp_path, mapping="3001:3001")

    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(compose_host, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(health, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(compose_host, "docker_compose_down", new_callable=AsyncMock) as mock_down, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="192.168.1.8"):
        mock_ai.side_effect = [
            _recipe(".vuln-env/docker-compose.yml", "http://localhost:3001"),
            _recipe(".vuln-env/docker-compose.yml", "http://127.0.0.1:3001"),
        ]
        mock_up.return_value = (True, "")
        mock_logs.return_value = "app exited 1"
        mock_hc.side_effect = [(False, None, "http"), (True, 3001, "http")]

        out = await mod.EnvReadyNode().execute(ctx)

    assert mock_ai.call_count == 2
    assert mock_down.await_count == 1
    assert mock_ai.call_args_list[1].kwargs["failed_stage"] == "health_check"
    assert "健康检查不过" in mock_ai.call_args_list[1].args[2]
    assert "无 HTTP 应答" in mock_ai.call_args_list[1].args[2]
    assert "app exited 1" in mock_ai.call_args_list[1].args[2]
    assert out["target_url"] == "http://192.168.1.8:3001"


def test_parse_compose_host_ports_short_and_long():
    from app.contexts.agent.nodes.env_ready.ports import parse_compose_port_mappings, web_host_ports

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


def test_compose_port_declaration_accepts_dynamic_tcp_but_not_udp():
    from app.contexts.agent.nodes.env_ready.ports import (
        compose_declares_web_port,
        parse_compose_port_mappings,
    )

    dynamic = """
services:
  web:
    ports:
      - "3000"
"""
    udp_only = """
services:
  web:
    ports:
      - target: 3000
        published: 3001
        protocol: udp
"""

    assert parse_compose_port_mappings(dynamic) == []
    assert compose_declares_web_port(dynamic) is True
    assert compose_declares_web_port(udp_only) is False


def test_runtime_bindings_reject_loopback_only_and_database_ports():
    from app.contexts.agent.nodes.env_ready.ports import (
        publishable_runtime_bindings,
        web_runtime_bindings,
    )

    bindings = web_runtime_bindings(
        [
            {
                "host_ip": "127.0.0.1",
                "host_port": 49153,
                "container_port": 3000,
                "protocol": "tcp",
            },
            {
                "host_ip": "0.0.0.0",
                "host_port": 49154,
                "container_port": 5432,
                "protocol": "tcp",
            },
            {
                "host_ip": "0.0.0.0",
                "host_port": 49155,
                "container_port": 8080,
                "protocol": "tcp",
            },
        ]
    )

    assert publishable_runtime_bindings(bindings, "10.0.0.8") == [
        {
            "host_ip": "0.0.0.0",
            "host_port": 49155,
            "container_port": 8080,
            "protocol": "tcp",
            "probe_host": "127.0.0.1",
            "public_host": "10.0.0.8",
        }
    ]


def _urlopen_cm(body: str, status: int = 200):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body.encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


_ZENTAO_FATAL = (
    "Fatal error: Uncaught PDOException: SQLSTATE[42S02]: Base table or view not found: "
    "1146 Table 'zentao.zt_config' doesn't exist ,the sql is: 'SELECT `value` FROM `zt_config`'"
)


@pytest.mark.parametrize(
    ("body", "expect_alive"),
    [
        (_ZENTAO_FATAL, False),
        ("<html><title>Whitelabel Error Page</title></html>", False),
        ("Traceback (most recent call last):\n  File app.py", False),
        ("Error establishing a database connection", False),
        ("<html><title>禅道</title><body>登录</body></html>", True),
        ("<html><form action='/login'>username</form></html>", True),
        ("ok", True),
    ],
)
def test_http_alive_rejects_crash_homepage(body, expect_alive):
    """探活必须读首页正文：HTTP 200 但 Fatal/缺表不能当就绪。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    with patch("urllib.request.urlopen", return_value=_urlopen_cm(body)):
        assert health._http_alive("http://127.0.0.1:8080") is expect_alive


@pytest.mark.parametrize(("status", "expected"), [(401, True), (403, True), (404, False)])
def test_http_alive_accepts_auth_gate_but_rejects_missing_route(status, expected):
    import io
    import urllib.error

    from app.contexts.agent.nodes.env_ready import health

    error = urllib.error.HTTPError(
        "http://127.0.0.1:8080/",
        status,
        "probe",
        {},
        io.BytesIO(b"ordinary response"),
    )
    with patch("urllib.request.urlopen", side_effect=error):
        assert health._http_alive("http://127.0.0.1:8080/") is expected


@pytest.mark.asyncio
async def test_health_check_settles_before_first_probe():
    """compose up 后端口未立刻 bind，先等 3s 再探。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    events: list[tuple] = []

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))

    async def fake_probe(url: str, timeout: float = 5) -> tuple[bool, str]:
        events.append(("probe", url))
        return True, ""

    with (
        patch.object(health, "_probe_http_async", fake_probe),
        patch.object(health, "HEALTH_SETTLE_SECONDS", 3),
        patch.object(health, "HEALTH_RETRIES", 1),
        patch.object(health, "HEALTH_RETRY_SECONDS", 0),
        patch.object(mod.asyncio, "sleep", fake_sleep),
    ):
        ok, port, scheme = await health.health_check([3001])
    assert ok is True
    assert port == 3001
    assert scheme == "http"
    assert events[0] == ("sleep", 3)
    assert events[1] == ("probe", "http://127.0.0.1:3001/")


@pytest.mark.asyncio
async def test_health_check_records_crash_body_for_ai_feedback():
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    with (
        patch.object(
            health,
            "_probe_http_async",
            new_callable=AsyncMock,
            return_value=(False, "首页内容异常: zt_config"),
        ),
        patch.object(health, "HEALTH_SETTLE_SECONDS", 0),
        patch.object(health, "HEALTH_RETRIES", 1),
        patch.object(health, "HEALTH_RETRY_SECONDS", 0),
    ):
        result = await health.health_check([8080])
        ok, port, scheme = result
    assert ok is False
    assert port is None
    assert "zt_config" in health.failure_reason(result)


@pytest.mark.asyncio
async def test_health_check_keeps_crash_body_over_fallback_tls_connection_error():
    """HTTP 正文根因不能被随后备用 HTTPS 的连接失败覆盖。"""
    from app.contexts.agent.nodes.env_ready import health

    async def fake_probe(url: str, timeout: float = 5) -> tuple[bool, str]:
        if url.startswith("http://"):
            return False, "首页内容异常: Fatal error: missing table"
        return False, "无 HTTP 应答: ConnectError: connection refused"

    with patch.object(health, "_probe_http_async", fake_probe):
        result = await health.health_check(
            [8080], retries=1, retry_seconds=0, settle_seconds=0
        )

    assert "Fatal error: missing table" in health.failure_reason(result)
    assert "ConnectError" not in health.failure_reason(result)


@pytest.mark.asyncio
async def test_health_check_failure_reason_isolated_between_concurrent_labs():
    """并发探活的错误必须跟随各自结果，不能通过函数属性互相覆盖。"""
    from app.contexts.agent.nodes.env_ready import health

    async def fake_probe(url: str, timeout: float = 5) -> tuple[bool, str]:
        if ":3001" in url:
            await asyncio.sleep(0.01)
            return False, "lab-a connection refused"
        await asyncio.sleep(0)
        return False, "lab-b database starting"

    with patch.object(health, "_probe_http_async", fake_probe):
        first, second = await asyncio.gather(
            health.health_check(
                [3001], retries=1, retry_seconds=0, settle_seconds=0
            ),
            health.health_check(
                [3002], retries=1, retry_seconds=0, settle_seconds=0
            ),
        )

    assert health.failure_reason(first).endswith("lab-a connection refused")
    assert health.failure_reason(second).endswith("lab-b database starting")


@pytest.mark.asyncio
async def test_health_check_does_not_scan_host_common_ports():
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    seen: list[str] = []

    async def fake_probe(url: str, timeout: float = 5) -> tuple[bool, str]:
        seen.append(url)
        return False, "无 HTTP 应答"

    with (
        patch.object(health, "_probe_http_async", fake_probe),
        patch.object(health, "HEALTH_SETTLE_SECONDS", 0),
        patch.object(health, "HEALTH_RETRIES", 1),
        patch.object(health, "HEALTH_RETRY_SECONDS", 0),
    ):
        ok, port, scheme = await health.health_check([3001])
    assert ok is False
    assert port is None
    assert scheme == "http"
    assert seen == [
        "http://127.0.0.1:3001/",
        "https://127.0.0.1:3001/",
    ]


@pytest.mark.asyncio
async def test_health_check_probes_https_for_tls_container_port():
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    seen: list[str] = []

    async def fake_probe(url: str, timeout: float = 5) -> tuple[bool, str]:
        seen.append(url)
        return ("https" in url), ""

    with (
        patch.object(health, "_probe_http_async", fake_probe),
        patch.object(health, "HEALTH_SETTLE_SECONDS", 0),
        patch.object(health, "HEALTH_RETRIES", 1),
        patch.object(health, "HEALTH_RETRY_SECONDS", 0),
    ):
        ok, port, scheme = await health.health_check(
            [8443, 3001], container_ports=[8443, 3000]
        )
    assert ok is True
    assert port == 8443
    assert scheme == "https"
    assert seen == ["https://127.0.0.1:8443/"]


def test_web_container_ports_keeps_alignment_with_host_ports():
    from app.contexts.agent.nodes.env_ready.ports import web_container_ports, web_host_ports

    mappings = [(3001, 3000), (8443, 8443), (3306, 3306), (3001, 3000)]
    assert web_host_ports(mappings) == [3001, 8443]
    assert web_container_ports(mappings) == [3000, 8443]


def test_publish_target_url_supports_https_scheme():
    from app.contexts.agent.target_url import publish_target_url

    assert publish_target_url(8443, advertise_ip="192.168.1.8", scheme="https") == (
        "https://192.168.1.8:8443"
    )
    assert publish_target_url(3001, advertise_ip="192.168.1.8") == "http://192.168.1.8:3001"


@pytest.mark.asyncio
async def test_reused_lab_alive_uses_target_url_scheme():
    from types import SimpleNamespace

    from app.contexts.agent.nodes.env_ready.reuse import _reused_lab_alive

    with patch(
        "app.contexts.agent.nodes.env_ready.health.health_check",
        new_callable=AsyncMock,
        return_value=(True, 8443, "https"),
    ) as health_check:
        ok = await _reused_lab_alive(
            SimpleNamespace(target_url="https://10.0.0.8:8443")
        )
    assert ok is True
    assert health_check.await_args.args[0] == [8443]
    assert health_check.await_args.kwargs["host_ips"] == ["10.0.0.8"]
    assert health_check.await_args.kwargs["preferred_scheme"] == "https"


def test_health_check_budget_covers_slow_jvm():
    from app.contexts.agent.nodes.env_ready.health import HEALTH_RETRIES, HEALTH_RETRY_SECONDS

    assert HEALTH_RETRIES * HEALTH_RETRY_SECONDS >= 90


@pytest.mark.parametrize(
    ("log", "must_keep", "must_drop"),
    [
        (
            "\n".join(
                [
                    "Image app Building",
                    "#13 downloading 40MB / 47MB",
                    "#20 [ERROR] Failed to execute goal on project producer",
                    "#20 [ERROR] Could not transfer artifact org.apache.logging.log4j:log4j-to-slf4j:jar:2.24.3",
                    "#20 [ERROR] Premature end of Content-Length delimited message body",
                    "#20 [ERROR] To see the full stack trace of the errors, re-run Maven with the -e switch.",
                    "#20 [ERROR] Re-run Maven using the -X switch to enable full debug logging.",
                    "#20 [ERROR] [Help 1] http://cwiki.apache.org/confluence/display/MAVEN/DependencyResolutionException",
                    "target producer: failed to solve: process \"/bin/sh -c mvn package\" did not complete successfully",
                ]
            ),
            "Could not transfer",
            "Re-run Maven",
        ),
        (
            "------\n > [build 3/6] COPY Eureka-Server ./Eureka-Server:\n------\nfailed to solve: not found",
            "COPY Eureka-Server",
            "Image Building",
        ),
        (
            "only progress lines\n#13 sha256:abc 30MB / 47MB",
            "30MB / 47MB",
            "Could not transfer",
        ),
    ],
)
def test_summarize_compose_failure_keeps_root_cause(log, must_keep, must_drop):
    from app.contexts.agent.nodes.env_ready.compose_host import summarize_compose_failure

    summary = summarize_compose_failure(log)
    assert must_keep in summary
    if must_drop != "Could not transfer":
        assert must_drop not in summary


def test_summarize_compose_failure_scans_past_database_noise():
    """Web 根因即使排在 2000 字符之后也不能被预截断。"""
    from app.contexts.agent.nodes.env_ready.compose_host import summarize_compose_failure

    log = ("mysql initialization progress\n" * 120) + (
        "web | Fatal error: database connection refused\n"
    )
    summary = summarize_compose_failure(log)
    assert "Fatal error" in summary
    assert "connection refused" in summary


def test_container_state_summary_keeps_healthcheck_output_without_env():
    from app.contexts.agent.nodes.env_ready.compose_host import _summarize_container_states

    raw = """[
      {
        "Name": "/project-web-1",
        "Config": {
          "Env": ["PASSWORD=must-not-leak"],
          "Labels": {"com.docker.compose.service": "web"}
        },
        "State": {
          "Status": "running",
          "ExitCode": 0,
          "OOMKilled": false,
          "Health": {
            "Status": "unhealthy",
            "Log": [{"ExitCode": 1, "Output": "curl: connection refused"}]
          }
        }
      }
    ]"""
    summary = _summarize_container_states(raw)
    assert "web: status=running" in summary
    assert "health=unhealthy" in summary
    assert "curl: connection refused" in summary
    assert "must-not-leak" not in summary


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("failed to solve: Dockerfile COPY missing", "compose_build"),
        ("Bind for 0.0.0.0:8080 failed: port in use", "port_conflict"),
        ("container web exited with code 1", "container_start"),
        ("container web is unhealthy", "container_healthcheck"),
        ("docker compose up 超时(>600s)", "compose_timeout"),
        ("docker compose 安全策略拒绝: privileged", "compose_policy"),
        ("Cannot connect to the Docker daemon", "docker_unavailable"),
    ],
)
def test_classify_compose_failure_stage(error, expected):
    from app.contexts.agent.nodes.env_ready.compose_host import (
        classify_compose_failure_stage,
    )

    assert classify_compose_failure_stage(error) == expected


@pytest.mark.asyncio
async def test_env_ready_url_uses_mapped_host_port_not_container_port(tmp_path):
    """AI 写了 3001:3000 却把 target_url 写成容器端口 3000 → 对外仍用宿主机映射口。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    _write_compose(tmp_path, mapping="3001:3000")
    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 3000})
    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(health, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"):
        mock_ai.return_value = _recipe(
            ".vuln-env/docker-compose.yml",
            "http://localhost:3000",
        )
        mock_up.return_value = (True, "")
        mock_hc.return_value = (True, 3001, "http")
        out = await mod.EnvReadyNode().execute(ctx)
    assert mock_hc.await_args.args[0] == [3001]
    assert out["target_url"] == "http://10.0.0.8:3001"


@pytest.mark.asyncio
async def test_env_ready_uses_docker_assigned_port_for_bare_mapping(tmp_path):
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import ai_recipe, compose_host, health, ports

    _write_compose(tmp_path, mapping="3000")
    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 3000})
    actual = [
        {
            "host_ip": "0.0.0.0",
            "host_port": 49153,
            "container_port": 3000,
            "protocol": "tcp",
        }
    ]
    with patch.object(
        ai_recipe, "run_ai_turn", new_callable=AsyncMock
    ) as mock_ai, patch.object(
        compose_host, "docker_compose_up", new_callable=AsyncMock
    ) as mock_up, patch.object(
        health, "health_check", new_callable=AsyncMock
    ) as mock_hc, patch.object(
        ports,
        "load_runtime_web_bindings",
        new_callable=AsyncMock,
        return_value=actual,
    ), patch(
        "app.contexts.agent.target_url.host_advertise_ip", return_value="10.0.0.8"
    ):
        mock_ai.return_value = _recipe(
            ".vuln-env/docker-compose.yml",
            "http://localhost:3000",
        )
        mock_up.return_value = (True, "")
        mock_hc.return_value = (True, 49153, "http")

        out = await mod.EnvReadyNode().execute(ctx)

    assert mock_hc.await_args.args[0] == [49153]
    assert out["target_url"] == "http://10.0.0.8:49153"


@pytest.mark.asyncio
async def test_env_ready_rejects_db_only_port_mapping(tmp_path):
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    _write_compose(tmp_path, mapping="5432:5432")
    ctx = _exec_ctx(tmp_path, {"is_web": True})
    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(compose_host, "collect_compose_logs", new_callable=AsyncMock, return_value=""), \
         patch.object(compose_host, "docker_compose_down", new_callable=AsyncMock) as mock_down:
        mock_ai.return_value = _recipe(
            ".vuln-env/docker-compose.yml",
            "http://localhost:5432",
        )
        mock_up.return_value = (True, "")
        out = await mod.EnvReadyNode().execute(ctx)
    assert out["ok"] is False
    assert mock_up.await_count == 0
    assert mock_down.await_count == 0
    assert "Web 端口" in mock_ai.call_args_list[-1].args[2]


def test_parse_docker_ps_published_ports_and_exclude_own_lab():
    from app.contexts.agent.nodes.env_ready.ports import parse_docker_ps_published_ports

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
async def test_run_ai_turn_passes_structured_retry_context():
    """下一轮只接收平台错误、失败阶段和端口状态，不依赖历史会话。"""
    from app.contexts.agent.contracts import EnvReadyInput
    from app.contexts.agent.contracts.outputs import ProfileHandoff, SourceHandoff
    from app.contexts.agent.nodes.env_ready.ai_recipe import run_ai_turn

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp",
        source_path="/tmp", vulnerability_description="d",
        project_address="x", project_ref=None,
        node_input=EnvReadyInput(
            source=SourceHandoff(repo_dirname="demo", workspace_path="/workspace/demo"),
            profile=ProfileHandoff(is_web=True),
        ),
    )
    with patch("app.contexts.agent.ai_runner.run_ai_node", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://127.0.0.1:3001",
        }
        await run_ai_turn(
            ctx,
            2,
            "connection refused",
            failed_stage="health_check",
            occupied_host_ports=[3001, 8080],
        )
    node_input = mock_run.await_args.kwargs["input_json"]
    assert node_input["previous_error"] == "connection refused"
    assert node_input["failed_stage"] == "health_check"
    assert node_input["occupied_host_ports"] == [3001, 8080]


@pytest.mark.asyncio
async def test_run_ai_turn_passes_credential_lookup_context():
    from app.contexts.agent.contracts import EnvReadyInput
    from app.contexts.agent.contracts.outputs import ProfileHandoff, SourceHandoff
    from app.contexts.agent.nodes.env_ready.ai_recipe import run_ai_turn

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp",
        source_path="/tmp", vulnerability_description="d",
        project_address="x", project_ref=None,
        node_input=EnvReadyInput(
            source=SourceHandoff(repo_dirname="demo", workspace_path="/workspace/demo"),
            profile=ProfileHandoff(is_web=True),
        ),
    )
    with patch("app.contexts.agent.ai_runner.run_ai_node", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://198.18.0.1:3001",
            "initial_creds": {"note": "需自行注册"},
        }
        await run_ai_turn(
            ctx,
            1,
            None,
            credential_lookup_only=True,
            existing_target_url="http://198.18.0.1:3001",
            existing_compose_path=".vuln-env/docker-compose.yml",
        )

    node_input = mock_run.await_args.kwargs["input_json"]
    assert node_input["credential_lookup_only"] is True
    assert node_input["existing_target_url"] == "http://198.18.0.1:3001"
    assert node_input["existing_compose_path"] == ".vuln-env/docker-compose.yml"


@pytest.mark.asyncio
async def test_env_ready_rejects_empty_initial_creds_before_compose_up(tmp_path):
    """AI 交空 initial_creds → 不起 compose，回喂补查。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})
    _write_compose(tmp_path)

    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(health, "health_check", new_callable=AsyncMock) as mock_hc:
        mock_ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:8000",
            "initial_creds": {},
        }

        out = await mod.EnvReadyNode().execute(ctx)

    assert out["ok"] is False
    assert mock_up.await_count == 0
    assert mock_hc.await_count == 0
    assert mock_ai.call_count == 5
    assert "initial_creds" in mock_ai.call_args_list[-1].args[2]


@pytest.mark.asyncio
async def test_env_ready_occupied_host_port_skips_compose_up(tmp_path):
    """配方里的宿主映射口已被其他容器占用 → 不起 compose，回喂 AI 改宿主侧端口。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    _write_compose(tmp_path, mapping="3001:3000")
    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 3000})
    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(health, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(ports, "list_docker_occupied_host_ports", return_value={3001}):
        mock_ai.return_value = _recipe(
            ".vuln-env/docker-compose.yml",
            "http://localhost:3001",
        )
        mock_up.return_value = (True, "")
        out = await mod.EnvReadyNode().execute(ctx)
    assert out["ok"] is False
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


@pytest.mark.asyncio
async def test_bump_node_attempt_writes_node_runs_attempt():
    """第 2 轮起把 attempt 写进 NodeRun；DB 异常只告警不炸排障循环。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    ctx = _exec_ctx("/tmp/w", {"is_web": True})
    ctx.db_session = session

    await create_loop._bump_node_attempt(ctx, 3)

    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "node_runs" in compiled or "node_index" in compiled
    session.commit.assert_awaited_once()

    session.execute.side_effect = RuntimeError("db down")
    await create_loop._bump_node_attempt(ctx, 4)  # 不抛


@pytest.mark.asyncio
async def test_env_ready_retry_bumps_attempt_per_round(tmp_path):
    """排障循环从第 2 轮起调用 _bump_node_attempt（attempt 随轮次递增）。"""
    from app.contexts.agent.nodes import env_ready as mod
    from app.contexts.agent.nodes.env_ready import (
        ai_recipe,
        compose_host,
        create_loop,
        health,
        ports,
    )

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})
    _write_compose(tmp_path, filename="1.yml")
    _write_compose(tmp_path, filename="2.yml")

    with patch.object(ai_recipe, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(compose_host, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(compose_host, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(health, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(compose_host, "docker_compose_down", new_callable=AsyncMock), \
         patch.object(create_loop, "_bump_node_attempt", new_callable=AsyncMock) as bump, \
         patch("app.contexts.agent.target_url.host_advertise_ip", return_value="192.168.1.8"):
        mock_ai.side_effect = [
            _recipe(".vuln-env/1.yml", "http://localhost:8000"),
            _recipe(".vuln-env/2.yml", "http://localhost:8000"),
        ]
        mock_up.side_effect = [(False, "fail"), (True, "")]
        mock_logs.return_value = ""
        mock_hc.return_value = (True, 8000, "http")

        out = await mod.EnvReadyNode().execute(ctx)

    assert mock_ai.call_count == 2
    assert bump.await_count == 1  # 第 2 轮才 bump
    bump.assert_awaited_with(ctx, 2)
    assert out["target_url"] == "http://192.168.1.8:8000"


@pytest.mark.asyncio
async def test_health_check_cancel_check_aborts_before_probing():
    """取消探测命中即返回，不再发起 HTTP 探测/重试（探活最长 30×3s，取消要秒级）。"""
    from app.contexts.agent.nodes.env_ready.health import health_check

    probes = {"n": 0}

    async def cancelled():
        probes["n"] += 1
        return True

    result = await health_check(
        [8080], retries=5, retry_seconds=0, settle_seconds=0,
        cancel_check=cancelled,
    )
    assert result.ok is False
    assert result.live_port is None
    assert "取消" in result.reason
    assert probes["n"] == 1


@pytest.mark.asyncio
async def test_wait_for_lab_aborts_on_cancelled_task():
    """等待共享靶场（最长 1860s）时任务被取消 → 立即抛错，不跑满超时。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.shared.base import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.shared.models import register_models

        register_models()
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    from app.contexts.agent.nodes.env_ready import _wait_for_lab
    from app.contexts.task.models import Task, TaskRun

    try:
        async with factory() as session:
            task = Task(project_address="x", task_type="discovery",
                        vulnerability_description=None, owner_id="u1",
                        status="cancelled")
            session.add(task)
            await session.flush()
            run = TaskRun(task_id=task.id, status="running")
            session.add(run)
            await session.commit()

            events: list[dict] = []
            ctx = NodeContext(
                task_id=task.id, run_id=run.id, host_workdir="/tmp/w",
                source_path="/tmp/w/repo", vulnerability_description="",
                project_address="x", project_ref=None,
                db_session=session, on_event=events.append,
            )
            with pytest.raises(RuntimeError, match="取消"):
                await _wait_for_lab(
                    ctx, owner_id="u1", project_id="p1", commit_sha="abc",
                )
            assert not any("等待其他任务" in str(e.get("message")) for e in events)
    finally:
        await engine.dispose()
