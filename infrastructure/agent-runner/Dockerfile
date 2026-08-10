# Crucible Agent Runner 基础镜像
#
# 用途：Claude Agent SDK 的隔离执行环境。Celery worker 通过 docker SDK 拉起本镜像，
#       容器内运行 python -m runner.run_one，事件流以 JSONL 形式写到 stdout。
#
# 设计原则：
# - 最小化：只装 python + git + curl + tini + claude-agent-sdk（无需 Node/npm/chromium）
# - claude-agent-sdk wheel 已自带 Claude Code CLI 二进制，无需单独安装
# - 非 root（uid=1000）+ 只读 rootfs + tmpfs /tmp + cap_drop ALL（由 worker 在 docker run 时叠加）
# - PYTHONUNBUFFERED=1 保证 JSONL 实时 flush（worker 端无需等容器退出就能消费）
# - HOME=/workspace 因 rootfs 只读，SDK/CLI 写 cache 到 $HOME/.claude
# - tini 作为 PID 1，回收 SDK fork 出的孤儿子进程
#
# 构建：
#   cd infrastructure
#   docker build -f agent-runner/Dockerfile -t crucible-agent-runner:base .

FROM python:3.11-slim

# 安装基础工具：git（被审计项目 clone）、curl（漏洞验证 PoC）、tini（PID 1 回收）
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# claude-agent-sdk（含 bundled Claude Code CLI）
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -c "from claude_agent_sdk import query; print('claude-agent-sdk loaded')"

# 非 root 用户（与 worker 端 docker run --user 1000:1000 对齐）
RUN useradd --create-home --uid 1000 --shell /bin/bash runner \
    && mkdir -p /workspace && chown -R runner:runner /workspace

# runner 入口脚本（容器内主程序）
COPY runner/ /workspace/runner/
RUN chown -R runner:runner /workspace/runner

WORKDIR /workspace
USER runner

# JSONL 实时 flush（双保险：Dockerfile ENV + worker docker run --env）
ENV PYTHONUNBUFFERED=1 \
    HOME=/workspace \
    PATH="/home/runner/.local/bin:${PATH}"

# tini → python -m runner.run_one
#   run_one 读 /workspace/.prompt.json → 调 SDK → JSONL 到 stdout
ENTRYPOINT ["tini", "--", "python", "-m", "runner.run_one"]