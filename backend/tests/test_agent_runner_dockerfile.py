"""agent-runner 镜像：Python + Node + 完整 Linux 命令（非 slim）。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "infrastructure" / "agent-runner" / "Dockerfile"

# 钉补丁号，禁止 python:3.11 / node:20-bookworm 浮动标签。
PINNED_PYTHON_FROM = "FROM python:3.11.15"
PINNED_NODE_FROM = "FROM node:20.20.2-bookworm AS nodejs"


def test_agent_runner_image_is_analyst_toolbox_not_slim():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "python:3.11-slim" not in text
    assert re.search(rf"^{re.escape(PINNED_PYTHON_FROM)}\s*$", text, re.M), (
        f"agent-runner 必须钉死 {PINNED_PYTHON_FROM}，不要用 python:3.11 浮动标签"
    )
    assert re.search(rf"^{re.escape(PINNED_NODE_FROM)}\s*$", text, re.M), (
        f"agent-runner 必须钉死 {PINNED_NODE_FROM}，不要用 node:20-bookworm 浮动标签"
    )
    lower = text.lower()
    assert "from node:" in lower or "nodejs" in lower
    assert "npm" in lower
    for pkg in ("procps", "iproute2", "jq", "unzip", "file", "iputils-ping"):
        assert pkg in text


def test_dockerfile_does_not_bake_node_skills():
    """Skill 由 worker -v 挂载；镜像不得 COPY 全量 node-skills。"""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY infrastructure/agent-runner/node-skills" not in text
    assert "COPY plugins/vuln-verify-expert" not in text
    assert "PLUGIN_DIR" not in text
    assert "/node-skill" in text
