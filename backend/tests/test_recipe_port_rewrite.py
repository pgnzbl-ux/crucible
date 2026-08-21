import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.env_ready.compose_host import is_docker_unavailable
from app.contexts.agent.nodes.env_ready.ports import rewrite_compose_host_ports


def test_rewrite_skips_when_free():
    text = 'services:\n  web:\n    ports:\n      - "3001:3000"\n'
    assert rewrite_compose_host_ports(text, set()) == text


def test_rewrite_host_side_only():
    text = 'services:\n  web:\n    ports:\n      - "3001:3000"\n'
    out = rewrite_compose_host_ports(text, {3001})
    assert out is not None
    assert "3000" in out
    assert "3001:3000" not in out.replace(" ", "")


def test_rewrite_returns_none_when_no_ports():
    assert rewrite_compose_host_ports("services: {}\n", {80}) is None


def test_docker_unavailable_detects_daemon():
    assert is_docker_unavailable(
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock"
    )
    assert is_docker_unavailable(
        "docker compose 异常: Cannot connect to the Docker daemon"
    )
    assert not is_docker_unavailable("failed to build: npm ci exited 1")


def test_docker_unavailable_ignores_build_log_docker_io():
    log = (
        "FROM docker.io/library/node:20\n"
        "COPY package.json /app/\n"
        "failed to solve: no such file or directory\n"
    )
    assert not is_docker_unavailable(log)
