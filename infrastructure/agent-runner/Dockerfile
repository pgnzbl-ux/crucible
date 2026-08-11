# Crucible Agent Runner 基础镜像
#
# 用途：Claude Agent SDK 的隔离执行环境。Celery worker 通过 docker SDK 拉起本镜像，
#       容器内运行 python -m runner.run_one，事件流以 JSONL 形式写到 stdout。
#
# 设计原则：
# - 最小化：只装 python + git + curl + tini + claude-agent-sdk（无需 Node/npm/chromium）
# - claude-agent-sdk wheel 已自带 Claude Code CLI 二进制，无需单独安装
# - 内置 vuln-verify-expert 插件（阶段化编排的载体，agent + skills）
# - 非 root（uid=1000）+ 只读 rootfs + tmpfs /tmp + cap_drop ALL（由 worker 在 docker run 时叠加）
# - PYTHONUNBUFFERED=1 保证 JSONL 实时 flush
# - HOME=/workspace 因 rootfs 只读，SDK/CLI 写 cache 到 $HOME/.claude
# - tini 作为 PID 1，回收 SDK fork 出的孤儿子进程
#
# 构建（build context = 项目根，以便 COPY plugins/ 与 runner/）：
#   cd <repo-root>
#   docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .

FROM python:3.11-slim

# 安装基础工具：git（被审计项目 clone）、curl（漏洞验证 PoC）、tini（PID 1 回收）
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# claude-agent-sdk（含 bundled Claude Code CLI）
COPY infrastructure/agent-runner/requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -c "from claude_agent_sdk import query; print('claude-agent-sdk loaded')"

# 非 root 用户（与 worker 端 docker run --user 1000:1000 对齐）
RUN useradd --create-home --uid 1000 --shell /bin/bash runner \
    && mkdir -p /workspace /app/plugins && chown -R runner:runner /workspace /app

# vuln-verify-expert 插件（阶段化 agent + skills，部署到容器供 SDK 加载）
# 放 /app/plugins/（只读，凭据零落盘：插件不含任何密钥）
COPY plugins/vuln-verify-expert /app/plugins/vuln-verify-expert

# runner 入口脚本（容器内主程序）
COPY infrastructure/agent-runner/runner/ /workspace/runner/
RUN chown -R runner:runner /workspace/runner

WORKDIR /workspace
USER runner

# JSONL 实时 flush（双保险：Dockerfile ENV + worker docker run --env）
# PLUGIN_DIR 供 run_one.py 加载插件（ClaudeAgentOptions.plugins / --plugin-dir）
ENV PYTHONUNBUFFERED=1 \
    HOME=/workspace \
    PLUGIN_DIR=/app/plugins/vuln-verify-expert \
    PLUGIN_NAME=vuln-verify-expert \
    AGENT_NAME=vuln-verify-expert \
    PATH="/home/runner/.local/bin:${PATH}"

# tini → python -m runner.run_one
#   run_one 读 /workspace/.prompt.json → 加载插件 + 指定 agent → JSONL 到 stdout
ENTRYPOINT ["tini", "--", "python", "-m", "runner.run_one"]
