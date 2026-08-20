#!/usr/bin/env bash
# Crucible Cloud Agent 环境公共函数：Docker daemon 启停与就绪等待。
# 由 install.sh / start.sh 复用。所有函数需幂等（可重复调用）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOCKER_LOG="/tmp/cursor/dockerd.log"

log() { echo "[crucible-env] $*"; }

# 非 systemd 环境（tini 作 PID1），dockerd 需手动拉起。
# 使用 fuse-overlayfs 存储驱动，规避嵌套容器 overlay 挂载失败。
ensure_dockerd() {
  if docker info >/dev/null 2>&1; then
    log "docker daemon 已就绪"
    return 0
  fi

  # daemon.json：关闭 containerd snapshotter，改用 fuse-overlayfs 图驱动
  sudo mkdir -p /etc/docker
  if [ ! -f /etc/docker/daemon.json ]; then
    echo '{"features":{"containerd-snapshotter":false},"storage-driver":"fuse-overlayfs"}' \
      | sudo tee /etc/docker/daemon.json >/dev/null
  fi

  mkdir -p "$(dirname "$DOCKER_LOG")"
  log "启动 dockerd（fuse-overlayfs）..."
  sudo nohup dockerd >"$DOCKER_LOG" 2>&1 &
  disown || true

  # 等待 socket 就绪（最长 ~60s）
  for _ in $(seq 1 60); do
    if sudo docker info >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done

  if ! sudo docker info >/dev/null 2>&1; then
    log "dockerd 启动失败，日志尾部："
    tail -30 "$DOCKER_LOG" || true
    return 1
  fi

  # 让运行用户（非 root）可直连 docker.sock —— 后端/worker 以当前用户身份用 docker SDK
  sudo chmod 666 /var/run/docker.sock || true
  log "docker daemon 启动完成"
}

# 等待基础设施容器 healthcheck 通过
wait_infra_healthy() {
  local tries=60
  for _ in $(seq 1 "$tries"); do
    local unhealthy
    unhealthy=$(cd "$REPO_ROOT/infrastructure" && docker compose ps --format '{{.Health}}' 2>/dev/null \
      | grep -Ev '^(healthy|)$' | wc -l | tr -d ' ')
    if [ "$unhealthy" = "0" ]; then
      log "基础设施容器 healthcheck 通过"
      return 0
    fi
    sleep 2
  done
  log "警告：部分基础设施容器未在预期时间内变为 healthy"
  (cd "$REPO_ROOT/infrastructure" && docker compose ps) || true
  return 0
}
