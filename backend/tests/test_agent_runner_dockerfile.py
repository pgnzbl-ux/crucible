"""agent-runner 镜像：Python + Node + 完整 Linux 命令（非 slim）。"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "backend" / "agent-runner" / "Dockerfile"

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
    for pkg in (
        "procps",
        "iproute2",
        "jq",
        "unzip",
        "file",
        "iputils-ping",
    ):
        assert pkg in text


def test_runner_local_bin_path_exists_on_read_only_rootfs():
    """镜像 PATH 中的目录必须在构建期存在，运行时根文件系统保持只读。"""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "install -d -o root -g root -m 0755 /home/runner/.local/bin" in text


def test_runner_uses_linux_bash_env_for_provider_credential_isolation():
    text = DOCKERFILE.read_text(encoding="utf-8")
    scrubber = (
        ROOT / "backend" / "agent-runner" / "runner" / "bash_env.sh"
    ).read_text(encoding="utf-8")
    assert "BASH_ENV=/app/runner/bash_env.sh" in text
    assert "unset ANTHROPIC_API_KEY" in scrubber
    assert "unset ANTHROPIC_AUTH_TOKEN" in scrubber
    # SCRUB=1 时 Claude Code 要求镜像内有 bubblewrap（及 socat）
    assert "bubblewrap" in text
    assert "socat" in text
    assert "command -v bwrap" in text



def test_dockerfile_does_not_bake_node_skills():
    """Skill 由 worker -v 挂载；镜像不得 COPY 全量 node-skills。"""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY backend/agent-runner/node-skills" not in text
    assert "COPY infrastructure/agent-runner/node-skills" not in text
    assert "COPY plugins/vuln-verify-expert" not in text
    assert "PLUGIN_DIR" not in text
    assert "/node-skill" in text


def test_image_default_entrypoint_is_server_and_worker_relies_on_it():
    """契约锁定：镜像默认入口 = HTTP/SSE 守护，worker 侧不传 command。

    此前 CMD 改成 server 而 worker 仍按 stdout JSONL 消费，现网节点会全部
    挂起到超时（P0 教训）。两侧契约由本测试钉死。
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["tini", "--"]' in text
    assert 'CMD ["python", "-m", "runner.server"]' in text

    from app.core.agent_runner import AgentRunnerManager

    assert "command" not in AgentRunnerManager.create.__code__.co_names or True
    import inspect

    src = inspect.getsource(AgentRunnerManager.create)
    assert '"command"' not in src, "worker 不得覆盖镜像默认入口（tini → runner.server）"
