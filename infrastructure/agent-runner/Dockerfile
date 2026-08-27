# Crucible Agent Runner 基础镜像
#
# 用途：Claude Agent SDK 的隔离执行环境（分析员工具箱）。Celery worker 通过
#       docker SDK 拉起本镜像，容器内运行 python -m runner.run_one。
#
# 设计原则：
# - Python 钉死 3.11.15、Node 钉死 20.20.2-bookworm（不用 3.11 / 20 浮动标签）。
#   不升 Node 24：runner 里 Node 只做 node -e 探测；skill 里的 node:20-slim 是靶场配方模板，另案处理。
#   靶场项目自己的运行时仍在 .vuln-env，本镜像只给 Agent 读源码 / 写配方 / 只读探测
#   （node -e、python -c），不 npm install、不起靶场。
# - 不装 Chromium / JDK / Go / PHP / docker CLI（docker compose 由宿主机 worker 执行）
# - claude-agent-sdk wheel 已自带 Claude Code CLI 二进制
# - 节点 SKILL.md **不进镜像**：worker 起容器时 -v 只挂当前节点
#   infrastructure/agent-runner/node-skills/<node> → /node-skill:ro
# - 非 root（uid=1000）+ 只读 rootfs + tmpfs /tmp + cap_drop ALL（worker docker run 叠加）
# - PYTHONUNBUFFERED=1 保证 JSONL 实时 flush
# - HOME=/tmp（容器 tmpfs）供 SDK/CLI 写 cache；不得用 /workspace，避免节点间共享会话
# - tini 作为 PID 1，回收 SDK fork 出的孤儿子进程
# - bubblewrap(+socat)：CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1 时 CLI 从 Bash 子进程剥凭据所需
#
# 构建（build context = 项目根）：
#   cd <repo-root>
#   docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .

FROM node:20.20.2-bookworm AS nodejs

FROM python:3.11.15

# GNU/Linux 常用命令（slim 经常没有 ps/ping/jq/file）
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        wget \
        ca-certificates \
        tini \
        bash \
        bubblewrap \
        socat \
        procps \
        psmisc \
        iproute2 \
        iputils-ping \
        bind9-dnsutils \
        netcat-openbsd \
        jq \
        unzip \
        zip \
        xz-utils \
        bzip2 \
        file \
        tree \
        make \
        patch \
        openssl \
        sqlite3 \
        gnupg \
        less \
        bsdextrautils \
        findutils \
        grep \
        sed \
        gawk \
        tar \
        gzip \
    && rm -rf /var/lib/apt/lists/* \
    && command -v bwrap \
    && command -v socat

# Node 20.20.2 + npm（从官方 node 镜像拷二进制，不污染 Python 的 pip）
COPY --from=nodejs /usr/local/bin/node /usr/local/bin/node
COPY --from=nodejs /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node -v && npm -v && python --version

# claude-agent-sdk（含 bundled Claude Code CLI）
COPY infrastructure/agent-runner/requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -c "from claude_agent_sdk import query; print('claude-agent-sdk loaded')"

# 非 root 用户（与 worker 端 docker run --user 1000:1000 对齐）
RUN useradd --create-home --uid 1000 --shell /bin/bash runner \
    && install -d -o root -g root -m 0755 /home/runner/.local/bin \
    && mkdir -p /workspace /app /node-skill && chown -R runner:runner /workspace /app

# vuln-verify-expert 桌面插件与节点 skill 均不进镜像。
# skill 由 worker -v 挂到 /node-skill:ro。

# runner 入口脚本必须在 /app（不可放 /workspace）：host_workdir bind mount
# 到 /workspace 会盖掉镜像文件，python -m runner.run_one 变成 ModuleNotFoundError。
COPY infrastructure/agent-runner/runner/ /app/runner/
RUN chown -R runner:runner /app/runner

WORKDIR /workspace
USER runner

# JSONL 实时 flush（双保险：Dockerfile ENV + worker docker run --env）
# PYTHONPATH=/app 让 python -m runner.run_one 找到 /app/runner（不依赖 cwd）
ENV PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    BASH_ENV=/app/runner/bash_env.sh \
    PYTHONPATH=/app \
    PATH="/usr/local/bin:/home/runner/.local/bin:${PATH}"

# tini → python -m runner.run_one
#   run_one 读 /workspace/.node.json + /node-skill/SKILL.md → JSONL 到 stdout
ENTRYPOINT ["tini", "--", "python", "-m", "runner.run_one"]
