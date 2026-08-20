#!/usr/bin/env bash
# Crucible Cloud Agent —— start 阶段（每次开机运行）。
# 要求：容忍重启、避免重复、检查就绪后返回。不在此装依赖或编译。
#
# 职责：拉起 docker daemon + 基础设施（PostgreSQL 5433 / Redis 6380 / MinIO 9000）。
# 应用服务器（API / worker / 前端）由 terminals 常驻运行。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/lib.sh"

ensure_dockerd

cd "$REPO_ROOT/infrastructure"
log "启动基础设施容器..."
docker compose up -d
wait_infra_healthy

log "start 阶段完成"
