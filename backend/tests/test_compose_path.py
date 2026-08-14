"""靶场 compose 路径必须落在 host_workdir/{仓库名} 下。"""
import sys
import os
from io import StringIO
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.env_ready import resolve_compose_host_path


def test_relative_path_uses_repo_dirname(tmp_path):
    repo = tmp_path / "claudecodeui" / ".vuln-env"
    repo.mkdir(parents=True)
    f = repo / "docker-compose.yml"
    f.write_text("x: 1")
    got = resolve_compose_host_path(
        ".vuln-env/docker-compose.yml", str(tmp_path), repo_dirname="claudecodeui"
    )
    assert got == str(f)


def test_container_absolute_workspace_path(tmp_path):
    project = tmp_path / "claudecodeui" / ".vuln-env"
    project.mkdir(parents=True)
    f = project / "docker-compose.yml"
    f.write_text("x: 1")
    got = resolve_compose_host_path(
        "/workspace/claudecodeui/.vuln-env/docker-compose.yml", str(tmp_path)
    )
    assert got == str(f)


def test_missing_file_still_points_at_repo(tmp_path):
    got = resolve_compose_host_path(
        ".vuln-env/x.yml", str(tmp_path), repo_dirname="claudecodeui"
    )
    assert got.replace("\\", "/").endswith("claudecodeui/.vuln-env/x.yml")


def test_empty_repo_dirname_uses_workdir_root(tmp_path):
    got = resolve_compose_host_path(
        ".vuln-env/docker-compose.yml", str(tmp_path), repo_dirname=None
    )
    assert got == str(tmp_path / ".vuln-env" / "docker-compose.yml")


def test_compose_progress_throttle_emits_first_urgent_and_flush():
    """构建日志很多，只把首行、失败、以及节流窗口末行推给前端。"""
    from app.contexts.agent.nodes.env_ready import ComposeProgressThrottle

    emitted: list[str] = []
    t = ComposeProgressThrottle(emitted.append, min_interval=10.0)
    t.push("  Building web  ")
    t.push("#2 CACHED")
    t.push("ERROR: failed to solve")
    t.push("exporting to image")
    t.flush()
    assert emitted[0] == "Building web"
    assert any("ERROR" in x for x in emitted)
    assert emitted[-1] == "exporting to image"


@pytest.mark.asyncio
async def test_compose_up_uses_lab_project_name(tmp_path):
    """up 必须带 -p crucible-lab-{lab_id}，否则巡检扫不到历史靶场。"""
    from app.contexts.agent.nodes.env_ready import docker_compose_up
    from app.contexts.agent.runtime_cleanup import lab_project_name

    compose = tmp_path / "repo" / ".vuln-env"
    compose.mkdir(parents=True)
    (compose / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    fake = MagicMock()
    fake.stdout = StringIO("Started\n")
    fake.wait.return_value = 0
    with patch("app.contexts.agent.nodes.env_ready.subprocess.Popen", return_value=fake) as popen:
        ok, err = await docker_compose_up(
            ".vuln-env/docker-compose.yml", str(tmp_path), "repo", lab_id="Lab-1"
        )
    assert ok and err == ""
    cmd = popen.call_args.args[0]
    assert "-p" in cmd
    assert cmd[cmd.index("-p") + 1] == lab_project_name("Lab-1")
    assert cmd[cmd.index("-p") + 1] == "crucible-lab-lab-1"


@pytest.mark.asyncio
async def test_compose_up_streams_plain_progress(tmp_path):
    """无 TTY 时必须 --progress plain，并把构建行回调出去，否则前端会假死。"""
    from app.contexts.agent.nodes.env_ready import docker_compose_up

    compose = tmp_path / "repo" / ".vuln-env"
    compose.mkdir(parents=True)
    (compose / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    fake = MagicMock()
    fake.stdout = StringIO("Building app\nStarted app\n")
    fake.wait.return_value = 0
    lines: list[str] = []
    with patch("app.contexts.agent.nodes.env_ready.subprocess.Popen", return_value=fake) as popen:
        ok, err = await docker_compose_up(
            ".vuln-env/docker-compose.yml",
            str(tmp_path),
            "repo",
            lab_id="Lab-1",
            on_progress=lines.append,
        )
    assert ok and err == ""
    cmd = popen.call_args.args[0]
    assert "--progress" in cmd
    assert cmd[cmd.index("--progress") + 1] == "plain"
    assert lines[0] == "Building app"
    assert "Started app" in lines


@pytest.mark.asyncio
async def test_compose_down_by_project_without_yaml(tmp_path):
    """workdir 已删时仍能按项目名 down。"""
    from app.contexts.agent.nodes.env_ready import docker_compose_down

    fake = MagicMock()
    fake.returncode = 0
    with patch("app.contexts.agent.nodes.env_ready.subprocess.run", return_value=fake) as run:
        await docker_compose_down(str(tmp_path / "missing"), lab_id="abc")
    cmd = run.call_args.args[0]
    assert cmd[:4] == ["docker", "compose", "-p", "crucible-lab-abc"]
    assert "down" in cmd
