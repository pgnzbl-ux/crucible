"""靶场 compose 路径必须落在 host_workdir/project 下。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.env_ready import resolve_compose_host_path


def test_relative_path_prefers_project_subdir(tmp_path):
    project = tmp_path / "project" / ".vuln-env"
    project.mkdir(parents=True)
    f = project / "docker-compose.yml"
    f.write_text("x: 1")
    got = resolve_compose_host_path(".vuln-env/docker-compose.yml", str(tmp_path))
    assert got == str(f)


def test_container_absolute_workspace_path(tmp_path):
    project = tmp_path / "project" / ".vuln-env"
    project.mkdir(parents=True)
    f = project / "docker-compose.yml"
    f.write_text("x: 1")
    got = resolve_compose_host_path(
        "/workspace/project/.vuln-env/docker-compose.yml", str(tmp_path)
    )
    assert got == str(f)


def test_missing_file_still_points_at_project(tmp_path):
    got = resolve_compose_host_path(".vuln-env/x.yml", str(tmp_path))
    assert got.replace("\\", "/").endswith("project/.vuln-env/x.yml")
