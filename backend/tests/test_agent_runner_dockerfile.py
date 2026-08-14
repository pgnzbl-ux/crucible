"""agent-runner 镜像：Python + Node + 完整 Linux 命令（非 slim）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "infrastructure" / "agent-runner" / "Dockerfile"


def test_agent_runner_image_is_analyst_toolbox_not_slim():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "python:3.11-slim" not in text
    assert "FROM python:3.11" in text
    lower = text.lower()
    assert "from node:" in lower or "nodejs" in lower
    assert "npm" in lower
    for pkg in ("procps", "iproute2", "jq", "unzip", "file", "iputils-ping"):
        assert pkg in text
