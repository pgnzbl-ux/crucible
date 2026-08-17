"""取消 / 任务结束只拆 agent-runner，不影响共享靶场。"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_lab_project_name_is_stable_and_prefixed():
    from app.contexts.agent.runtime_cleanup import lab_project_name, task_id_from_lab_project

    name = lab_project_name("AbC-123")
    assert name == "crucible-lab-abc-123"
    assert task_id_from_lab_project(name) == "abc-123"
    assert task_id_from_lab_project("postgres") is None


@pytest.mark.parametrize(
    "status,age,timeout,keep",
    [
        ("running", 10, 1800, True),
        ("queued", 100, 1800, True),
        ("pending", 0, 1800, True),
        ("completed", 10, 1800, False),
        ("failed", 10, 1800, False),
        ("cancelled", 10, 1800, False),
        ("archived", 10, 1800, False),
        ("running", 1801, 1800, False),
        (None, 10, 1800, False),
    ],
)
def test_should_keep_runtime(status, age, timeout, keep):
    from app.contexts.agent.runtime_cleanup import should_keep_runtime

    assert should_keep_runtime(status, age, timeout) is keep


def test_utc_unix_treats_naive_datetime_as_utc_not_local():
    """SQLite 存的是 naive UTC；datetime.timestamp() 会当本地时，东八区会把 running 任务算超龄 8 小时。"""
    from datetime import datetime, timezone

    from app.contexts.agent.runtime_cleanup import utc_unix

    naive = datetime(2026, 8, 13, 12, 19, 57)
    aware = datetime(2026, 8, 13, 12, 19, 57, tzinfo=timezone.utc)
    assert utc_unix(naive) == aware.timestamp()
    assert utc_unix(None) is None
    assert utc_unix(aware) == aware.timestamp()


@pytest.mark.asyncio
async def test_teardown_task_runtime_only_removes_agent_runner():
    """按 task_id 只拆该任务的 agent-runner，不拆共享靶场。"""
    from app.contexts.agent.runtime_cleanup import teardown_task_runtime

    with patch(
        "app.contexts.agent.runtime_cleanup.agent_runner_manager"
    ) as mock_mgr, patch(
        "app.contexts.agent.nodes.env_ready.docker_compose_down",
        new_callable=AsyncMock,
    ) as mock_down:
        mock_mgr.host_workdir_path.return_value = r"D:\tmp\crucible\audit-abc"
        await teardown_task_runtime("abc")

    mock_mgr.host_workdir_path.assert_called_once_with("abc")
    mock_down.assert_not_awaited()
    mock_mgr.remove_for_task.assert_called_once_with("abc", r"D:\tmp\crucible\audit-abc")


def test_new_run_does_not_compose_down_shared_lab():
    """重试清空任务 workspace 时不得按旧 task_id 去 down 共享靶场。"""
    import inspect

    from app.contexts.agent import tasks as tasks_mod

    source = inspect.getsource(tasks_mod._run_analysis)
    assert "docker_compose_down" not in source


@pytest.mark.asyncio
async def test_teardown_only_kills_runner():
    """取消时 AI 还在跑：强拆 agent-runner，但不拆共享 compose。"""
    from app.contexts.agent.runtime_cleanup import teardown_task_runtime

    order: list[str] = []

    def remove_for_task(task_id, workdir):
        order.append("runner")
        return 1

    with patch(
        "app.contexts.agent.runtime_cleanup.agent_runner_manager"
    ) as mock_mgr, patch(
        "app.contexts.agent.nodes.env_ready.docker_compose_down",
        new_callable=AsyncMock,
    ) as mock_down:
        mock_mgr.host_workdir_path.return_value = r"D:\tmp\crucible\audit-abc"
        mock_mgr.remove_for_task.side_effect = remove_for_task
        await teardown_task_runtime("abc")

    assert order == ["runner"]
    mock_down.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_keeps_live_tears_down_the_rest():
    """巡检：活着且未超时的保留；已结束 / 超时 / 库里没有的拆掉。"""
    from app.contexts.agent.runtime_cleanup import sweep_orphan_runtimes

    torn = []

    async def fake_teardown(task_id: str) -> None:
        torn.append(task_id)

    now = 10_000.0
    timeout = 1800
    await sweep_orphan_runtimes(
        discovered_ids={"live", "done", "stuck", "ghost"},
        status_by_id={
            "live": ("running", now - 60),
            "done": ("completed", now - 10),
            "stuck": ("running", now - 5000),
        },
        now=now,
        timeout_seconds=timeout,
        teardown=fake_teardown,
    )
    assert set(torn) == {"done", "stuck", "ghost"}
    assert "live" not in torn


def test_remove_for_workdir_only_touches_matching_mount():
    """只删 bind 了本任务 host_workdir 的 agent-runner，不动别的任务。"""
    from app.core.agent_runner import AgentRunnerManager

    mine = MagicMock()
    mine.id = "cid-mine"
    mine.attrs = {
        "Mounts": [{"Source": r"D:\tmp\crucible\audit-abc", "Destination": "/workspace"}]
    }
    other = MagicMock()
    other.id = "cid-other"
    other.attrs = {
        "Mounts": [{"Source": r"D:\tmp\crucible\audit-zzz", "Destination": "/workspace"}]
    }

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._client.containers.list.return_value = [mine, other]
    mgr._active_ids = {"cid-mine", "cid-other"}

    with patch.object(mgr, "remove_by_id") as mock_rm:
        removed = mgr.remove_for_workdir(r"D:\tmp\crucible\audit-abc")

    assert removed == 1
    mock_rm.assert_called_once_with("cid-mine")


def test_remove_for_task_matches_label_or_workdir():
    """按 crucible.task_id 标签拆 runner，避免只靠挂载路径漏网。"""
    from app.core.agent_runner import AgentRunnerManager

    labeled = MagicMock()
    labeled.id = "cid-label"
    labeled.labels = {"crucible.task_id": "abc", "managed_by": "crucible-agent-runner"}
    labeled.attrs = {"Mounts": []}

    mgr = AgentRunnerManager.__new__(AgentRunnerManager)
    mgr._client = MagicMock()
    mgr._client.containers.list.return_value = [labeled]
    mgr._active_ids = set()

    with patch.object(mgr, "remove_by_id") as mock_rm, patch.object(
        mgr, "remove_for_workdir", return_value=0
    ):
        removed = mgr.remove_for_task("abc", r"D:\tmp\crucible\audit-abc")
    assert removed >= 1
    mock_rm.assert_called_once_with("cid-label")


@pytest.mark.parametrize(
    "path,base,expected",
    [
        (r"D:\tmp\crucible\audit-abc-123", "/tmp/crucible/audit", "abc-123"),
        ("/tmp/crucible/audit-abc-123", "/tmp/crucible/audit", "abc-123"),
        (
            r"D:\tmp\crucible\audit-abc-123\claudecodeui",
            "/tmp/crucible/audit",
            "abc-123",
        ),
        ("/var/lib/docker/volumes/x/_data", "/tmp/crucible/audit", None),
        ("", "/tmp/crucible/audit", None),
    ],
)
def test_task_id_from_host_path(path, base, expected):
    from app.contexts.agent.runtime_cleanup import task_id_from_host_path

    assert task_id_from_host_path(path, base) == expected


def _container(labels=None, mounts=None):
    c = MagicMock()
    c.labels = labels or {}
    c.attrs = {"Mounts": [{"Source": src, "Destination": "/x"} for src in (mounts or [])]}
    return c


def test_collect_task_ids_from_docker_not_yaml():
    """任务标签 / 挂载路径算运行时；compose 项目名与残留 yaml 不算。"""
    from app.contexts.agent.runtime_cleanup import collect_task_ids_from_containers

    base = "/tmp/crucible/audit"
    ids = collect_task_ids_from_containers(
        [
            _container(labels={"crucible.task_id": "task-a"}),
            _container(labels={"com.docker.compose.project": "crucible-lab-task-b"}),
            _container(
                labels={"com.docker.compose.project": "vuln-env"},
                mounts=[r"D:\tmp\crucible\audit-task-c\claudecodeui"],
            ),
            _container(labels={"com.docker.compose.project": "crucible-infra"}),
        ],
        base,
    )
    assert ids == {"task-a", "task-c"}


def test_collect_ids_ignores_lab_compose_project():
    from types import SimpleNamespace

    from app.contexts.agent.runtime_cleanup import collect_task_ids_from_containers

    c = SimpleNamespace(
        labels={
            "com.docker.compose.project": "crucible-lab-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        },
        attrs={"Mounts": []},
    )
    assert collect_task_ids_from_containers([c], "/tmp/crucible/audit") == set()


def test_list_managed_ids_ignores_leftover_compose_yaml(tmp_path):
    """失败任务留下的 docker-compose.yml 不是孤儿运行时。"""
    from app.contexts.agent.runtime_cleanup import list_managed_task_ids

    ghost = "2ee93205-0abb-49f7-8978-f416c2b7d135"
    recipe = tmp_path / f"audit-{ghost}" / "claudecodeui" / ".vuln-env"
    recipe.mkdir(parents=True)
    (recipe / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    mock_mgr = MagicMock()
    mock_mgr._client.containers.list.return_value = []
    mock_settings = MagicMock()
    mock_settings.agent_runner_workdir_base = str(tmp_path / "audit")

    with patch(
        "app.contexts.agent.runtime_cleanup.agent_runner_manager", mock_mgr
    ), patch(
        "app.contexts.agent.runtime_cleanup.get_settings", return_value=mock_settings
    ):
        ids = list_managed_task_ids()
    assert ghost not in ids
    assert ids == set()


@pytest.mark.asyncio
async def test_legacy_lab_orphans_are_down_but_known_labs_are_kept():
    """历史 task compose 要清；大小写不同的现存 lab compose 不能误拆。"""
    from app.contexts.agent.runtime_cleanup import cleanup_legacy_lab_projects

    downed: list[str] = []

    async def fake_down(project: str) -> None:
        downed.append(project)

    await cleanup_legacy_lab_projects(
        {
            "crucible-lab-old-task",
            "Crucible-Lab-LAB-ABC",
            "postgres",
            "crucible-lab-",
        },
        {"lab-abc"},
        down=fake_down,
    )

    assert downed == ["crucible-lab-old-task"]


@pytest.mark.asyncio
async def test_lab_sweep_phases_continue_after_expire_failure():
    """TTL 阶段异常不能跳过 creating 与历史孤儿阶段。"""
    from app.contexts.agent.runtime_cleanup import run_lab_lifecycle_phases

    service = MagicMock()
    service.expire_silent_labs = AsyncMock(side_effect=RuntimeError("ttl failed"))
    service.fail_stale_creating = AsyncMock()
    service.cleanup_terminal_runtimes = AsyncMock()
    service.known_lab_ids = AsyncMock(return_value={"known"})
    service.session.rollback = AsyncMock()

    with patch(
        "app.contexts.agent.runtime_cleanup.list_lab_compose_projects",
        new_callable=AsyncMock,
        return_value={"crucible-lab-legacy"},
    ), patch(
        "app.contexts.agent.runtime_cleanup.cleanup_legacy_lab_projects",
        new_callable=AsyncMock,
    ) as cleanup:
        await run_lab_lifecycle_phases(service)

    service.fail_stale_creating.assert_awaited_once()
    service.cleanup_terminal_runtimes.assert_awaited_once()
    cleanup.assert_awaited_once_with({"crucible-lab-legacy"}, {"known"})
