#!/usr/bin/env bash
# Crucible Cloud Agent —— install 阶段（源码 checkout 后运行一次，用于生成快照基线）。
# 要求：幂等、非交互、必须终止。不在此启动长驻开发服务器（见 terminals）。
#
# 职责：
#   1. 保证系统级依赖存在（docker / fuse-overlayfs / python venv / 构建工具）
#   2. 后端 Python venv + 依赖
#   3. 前端 npm 依赖
#   4. 预拉取基础设施镜像 + 构建 agent-runner 镜像（缓存进快照，加速每次启动）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

cd "$REPO_ROOT"

# ── 1. 系统依赖（幂等：缺什么装什么）──
NEED_APT=0
command -v docker >/dev/null 2>&1 || NEED_APT=1
command -v fuse-overlayfs >/dev/null 2>&1 || NEED_APT=1
python3 -c 'import venv' >/dev/null 2>&1 || NEED_APT=1
if [ "$NEED_APT" = "1" ]; then
  log "安装系统依赖（docker.io / fuse-overlayfs / python venv / 构建工具）..."
  sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    docker.io docker-compose-v2 fuse-overlayfs fuse3 \
    python3-venv python3-dev build-essential git curl
  # fuse3 首次安装可能停在 conffile 交互，强制保留旧配置
  sudo DEBIAN_FRONTEND=noninteractive dpkg --configure -a --force-confold || true
  sudo groupadd -f docker
  sudo usermod -aG docker "$(whoami)" || true
fi

# ── 2. 拉起 docker daemon（构建/拉取镜像需要）──
ensure_dockerd

# ── 3. 后端：venv + 依赖 ──
cd "$REPO_ROOT/backend"
[ -f .env ] || cp .env.example .env
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip -q
./.venv/bin/pip install -e ".[dev]"

# ── 4. 前端：npm 依赖 ──
cd "$REPO_ROOT/frontend"
npm install --no-fund --no-audit

# ── 5. 基础设施镜像预拉取 + agent-runner 镜像构建（幂等）──
cd "$REPO_ROOT/infrastructure"
docker compose pull --quiet || log "警告：compose pull 未完全成功，start 阶段会重试"

cd "$REPO_ROOT"
if docker image inspect crucible-agent-runner:base >/dev/null 2>&1; then
  log "agent-runner 镜像已存在，跳过构建"
else
  log "构建 agent-runner 镜像（首次较慢）..."
  docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .
fi

log "install 阶段完成"
