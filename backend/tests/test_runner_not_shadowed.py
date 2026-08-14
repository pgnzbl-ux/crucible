"""agent-runner 入口不得放在 /workspace 下。

host_workdir bind mount 到 /workspace 后，镜像里 /workspace/runner 会被宿主机
空目录盖掉，python -m runner.run_one 变成 ModuleNotFoundError: runner。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "infrastructure" / "agent-runner" / "Dockerfile"


def test_dockerfile_copies_runner_outside_workspace():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY infrastructure/agent-runner/runner/ /app/runner/" in text
    assert "/workspace/runner" not in text
    assert "PYTHONPATH=/app" in text


def test_runner_package_has_init():
    init = ROOT / "infrastructure" / "agent-runner" / "runner" / "__init__.py"
    assert init.is_file(), "python -m runner.run_one 需要 runner 包含 __init__.py"
