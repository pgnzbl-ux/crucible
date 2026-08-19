"""节点 2 靶场就绪 — 排障循环测试(mock docker + AI)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from types import SimpleNamespace

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
        LS.return_value.download_recipe = AsyncMock(return_value=None)
        LS.return_value.upload_recipe = AsyncMock()
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
        mock_ai.return_value = _recipe(".vuln-env/x.yml", "http://localhost:8000")
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

    with patch("urllib.request.urlopen", return_value=_urlopen_cm(body)):
        assert mod._http_alive("http://127.0.0.1:8080") is expect_alive


@pytest.mark.asyncio
async def test_health_check_settles_before_first_probe():
    """compose up 后端口未立刻 bind，先等 3s 再探。"""
    from app.contexts.agent.nodes import env_ready as mod

    events: list[tuple] = []

    async def fake_sleep(seconds):
        events.append(("sleep", seconds))

    def fake_alive(url: str, timeout: float = 5) -> bool:
        events.append(("probe", url))
        return True

    with (
        patch.object(mod, "_http_alive", fake_alive),
        patch.object(mod, "HEALTH_SETTLE_SECONDS", 3),
        patch.object(mod, "HEALTH_RETRIES", 1),
        patch.object(mod, "HEALTH_RETRY_SECONDS", 0),
        patch.object(mod.asyncio, "sleep", fake_sleep),
    ):
        ok, port, scheme = await mod.health_check([3001])
    assert ok is True
    assert port == 3001
    assert scheme == "http"
    assert events[0] == ("sleep", 3)
    assert events[1] == ("probe", "http://127.0.0.1:3001")


@pytest.mark.asyncio
async def test_health_check_records_crash_body_for_ai_feedback():
    from app.contexts.agent.nodes import env_ready as mod

    with (
        patch("urllib.request.urlopen", return_value=_urlopen_cm(_ZENTAO_FATAL)),
        patch.object(mod, "HEALTH_SETTLE_SECONDS", 0),
        patch.object(mod, "HEALTH_RETRIES", 1),
        patch.object(mod, "HEALTH_RETRY_SECONDS", 0),
    ):
        ok, port, scheme = await mod.health_check([8080])
    assert ok is False
    assert port is None
    assert "zt_config" in (getattr(mod.health_check, "last_error", "") or "")


@pytest.mark.asyncio
async def test_health_check_does_not_scan_host_common_ports():
    from app.contexts.agent.nodes import env_ready as mod

    seen: list[str] = []

    def fake_alive(url: str, timeout: float = 5) -> bool:
        seen.append(url)
        return False

    with (
        patch.object(mod, "_http_alive", fake_alive),
        patch.object(mod, "HEALTH_SETTLE_SECONDS", 0),
        patch.object(mod, "HEALTH_RETRIES", 1),
        patch.object(mod, "HEALTH_RETRY_SECONDS", 0),
    ):
        ok, port, scheme = await mod.health_check([3001])
    assert ok is False
    assert port is None
    assert scheme == "http"
    assert seen == ["http://127.0.0.1:3001"]


@pytest.mark.asyncio
async def test_health_check_probes_https_for_tls_container_port():
    from app.contexts.agent.nodes import env_ready as mod

    seen: list[str] = []

    def fake_alive(url: str, timeout: float = 5) -> bool:
        seen.append(url)
        return "https" in url

    with (
        patch.object(mod, "_http_alive", fake_alive),
        patch.object(mod, "HEALTH_SETTLE_SECONDS", 0),
        patch.object(mod, "HEALTH_RETRIES", 1),
        patch.object(mod, "HEALTH_RETRY_SECONDS", 0),
    ):
        ok, port, scheme = await mod.health_check(
            [8443, 3001], container_ports=[8443, 3000]
        )
    assert ok is True
    assert port == 8443
    assert scheme == "https"
    assert seen == ["https://127.0.0.1:8443"]


def test_web_container_ports_keeps_alignment_with_host_ports():
    from app.contexts.agent.nodes.env_ready import web_container_ports, web_host_ports

    mappings = [(3001, 3000), (8443, 8443), (3306, 3306), (3001, 3000)]
    assert web_host_ports(mappings) == [3001, 8443]
    assert web_container_ports(mappings) == [3000, 8443]


def test_publish_target_url_supports_https_scheme():
    from app.contexts.agent.target_url import publish_target_url

    assert publish_target_url(8443, advertise_ip="192.168.1.8", scheme="https") == (
        "https://192.168.1.8:8443"
    )
    assert publish_target_url(3001, advertise_ip="192.168.1.8") == "http://192.168.1.8:3001"


def test_reused_lab_alive_uses_target_url_scheme():
    from types import SimpleNamespace

    from app.contexts.agent.nodes.env_ready import _reused_lab_alive

    with patch(
        "app.contexts.agent.nodes.env_ready._http_alive", return_value=True
    ) as alive:
        ok = _reused_lab_alive(
            SimpleNamespace(target_url="https://10.0.0.8:8443")
        )
    assert ok is True
    assert alive.call_args.args[0] == "https://127.0.0.1:8443"


def test_health_check_budget_covers_slow_jvm():
    from app.contexts.agent.nodes.env_ready import HEALTH_RETRIES, HEALTH_RETRY_SECONDS

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
    from app.contexts.agent.nodes.env_ready import summarize_compose_failure

    summary = summarize_compose_failure(log)
    assert must_keep in summary
    if must_drop != "Could not transfer":
        assert must_drop not in summary


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
async def test_env_ready_rejects_db_only_port_mapping(tmp_path):
    from app.contexts.agent.nodes import env_ready as mod

    _write_compose(tmp_path, mapping="5432:5432")
    ctx = _exec_ctx(tmp_path, {"is_web": True})
    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "collect_compose_logs", new_callable=AsyncMock, return_value=""), \
         patch.object(mod, "docker_compose_down", new_callable=AsyncMock) as mock_down:
        mock_ai.return_value = _recipe(
            ".vuln-env/docker-compose.yml",
            "http://localhost:5432",
        )
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
async def test_run_ai_turn_passes_structured_retry_context():
    """下一轮只接收平台错误、失败阶段和端口状态，不依赖历史会话。"""
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

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})
    _write_compose(tmp_path)

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc:
        mock_ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:8000",
            "initial_creds": {},
        }

        with pytest.raises(RuntimeError, match="5"):
            await mod.EnvReadyNode().execute(ctx)

    assert mock_up.await_count == 0
    assert mock_hc.await_count == 0
    assert mock_ai.call_count == 5
    assert "initial_creds" in mock_ai.call_args_list[-1].args[2]


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
        mock_ai.return_value = _recipe(
            ".vuln-env/docker-compose.yml",
            "http://localhost:3001",
        )
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


@pytest.mark.asyncio
async def test_bump_node_attempt_writes_node_runs_attempt():
    """第 2 轮起把 attempt 写进 NodeRun；DB 异常只告警不炸排障循环。"""
    from app.contexts.agent.nodes import env_ready as mod

    session = MagicMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    ctx = _exec_ctx("/tmp/w", {"is_web": True})
    ctx.db_session = session

    await mod._bump_node_attempt(ctx, 3)

    stmt = session.execute.await_args.args[0]
    compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "node_runs" in compiled or "node_index" in compiled
    session.commit.assert_awaited_once()

    session.execute.side_effect = RuntimeError("db down")
    await mod._bump_node_attempt(ctx, 4)  # 不抛


@pytest.mark.asyncio
async def test_env_ready_retry_bumps_attempt_per_round(tmp_path):
    """排障循环从第 2 轮起调用 _bump_node_attempt（attempt 随轮次递增）。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = _exec_ctx(tmp_path, {"is_web": True, "port": 8000})
    _write_compose(tmp_path, filename="1.yml")
    _write_compose(tmp_path, filename="2.yml")

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "collect_compose_logs", new_callable=AsyncMock) as mock_logs, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc, \
         patch.object(mod, "docker_compose_down", new_callable=AsyncMock), \
         patch.object(mod, "_bump_node_attempt", new_callable=AsyncMock) as bump, \
         patch.object(mod, "host_advertise_ip", return_value="192.168.1.8"):
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
