# Crucible Agent Runner 基础镜像
#
# 用途：Claude Agent SDK 的隔离执行环境（分析员工具箱）。Celery worker 通过
#       docker SDK 拉起本镜像，容器内运行 python -m runner.run_one。
#
# 设计原则：
# - Python + Node + 完整 Linux 命令（非 slim）。靶场项目自己的运行时仍在 .vuln-env，
#   本镜像只给 Agent 读源码 / 写配方 / 只读探测（node -e、python -c），不 npm install、不起靶场。
# - 不装 Chromium / JDK / Go / PHP / docker CLI（docker compose 由宿主机 worker 执行）
# - claude-agent-sdk wheel 已自带 Claude Code CLI 二进制
# - 内置 vuln-verify-expert 插件
# - 非 root（uid=1000）+ 只读 rootfs + tmpfs /tmp + cap_drop ALL（worker docker run 叠加）
# - PYTHONUNBUFFERED=1 保证 JSONL 实时 flush
# - HOME=/workspace 因 rootfs 只读，SDK/CLI 写 cache 到 $HOME/.claude
# - tini 作为 PID 1，回收 SDK fork 出的孤儿子进程
#
# 构建（build context = 项目根，以便 COPY plugins/ 与 runner/）：
#   cd <repo-root>
#   docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .

FROM node:20-bookworm AS nodejs

FROM python:3.11

# GNU/Linux 常用命令（slim 经常没有 ps/ping/jq/file）
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        wget \
        ca-certificates \
        tini \
        bash \
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
    && rm -rf /var/lib/apt/lists/*

# Node 20 + npm（从官方 node 镜像拷二进制，不污染 Python 的 pip）
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
    && mkdir -p /workspace /app/plugins && chown -R runner:runner /workspace /app

# vuln-verify-expert 插件（阶段化 agent + skills，部署到容器供 SDK 加载）
# 放 /app/plugins/（只读，凭据零落盘：插件不含任何密钥）
COPY plugins/vuln-verify-expert /app/plugins/vuln-verify-expert

# runner 入口脚本必须在 /app（不可放 /workspace）：host_workdir bind mount
# 到 /workspace 会盖掉镜像文件，python -m runner.run_one 变成 ModuleNotFoundError。
COPY infrastructure/agent-runner/runner/ /app/runner/
RUN chown -R runner:runner /app/runner

WORKDIR /workspace
USER runner

# JSONL 实时 flush（双保险：Dockerfile ENV + worker docker run --env）
# PLUGIN_DIR 供 run_one.py 加载插件（ClaudeAgentOptions.plugins / --plugin-dir）
# PYTHONPATH=/app 让 python -m runner.run_one 找到 /app/runner（不依赖 cwd）
ENV PYTHONUNBUFFERED=1 \
    HOME=/workspace \
    PYTHONPATH=/app \
    PLUGIN_DIR=/app/plugins/vuln-verify-expert \
    PLUGIN_NAME=vuln-verify-expert \
    AGENT_NAME=vuln-verify-expert \
    PATH="/usr/local/bin:/home/runner/.local/bin:${PATH}"

# tini → python -m runner.run_one
#   run_one 读 /workspace/.prompt.json → 加载插件 + 指定 agent → JSONL 到 stdout
ENTRYPOINT ["tini", "--", "python", "-m", "runner.run_one"]
