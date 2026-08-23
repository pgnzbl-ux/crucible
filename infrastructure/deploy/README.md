# Crucible 生产部署（路线 1：宿主机进程形态）

> 架构依据：`docs/development-guide.md` §2.5 / §5.4。**后端（API + Celery worker）必须跑在宿主机**——它通过 `docker.from_env()` 与 `subprocess docker compose` 直连宿主 Docker daemon 编排沙箱/靶场，且任务工作区（`/tmp/crucible/*`）依赖「后端进程 ↔ daemon 同一文件系统视图」的 bind mount 语义。禁止把后端装进容器（包括挂 `docker.sock` 的 sidecar）。

## 形态总览

| 组件 | 运行方式 | 托管 |
|---|---|---|
| PostgreSQL / Redis / MinIO | Docker 容器 | `infrastructure/docker-compose.yml` |
| 后端 API | 宿主机 systemd 进程 | `crucible-api.service` |
| Celery worker | 宿主机 systemd 进程 | `crucible-worker.service` |
| 前端 | Nginx 静态托管 `frontend/dist` | `nginx-crucible.conf` |
| agent-runner 沙箱 / Lab 靶场 | 宿主 Docker daemon 按需创建销毁 | 后端调度 |

## 前置

- Linux x86_64，Docker 20+ / Compose v2
- Python 3.11+、Node.js 18+、Nginx
- `/opt/crucible` 为部署根（代码 + venv）
- 运行用户 `crucible`（`useradd -r -m -d /opt/crucible -s /usr/sbin/nologin crucible`），加入 `docker` 组
- 域名 + TLS 证书（示例用 certbot）

## 部署步骤

```bash
# 0. 运行用户与目录
sudo useradd -r -m -d /opt/crucible -s /usr/sbin/nologin crucible
sudo usermod -aG docker crucible
sudo mkdir -p /opt/crucible && sudo chown crucible:crucible /opt/crucible

# 1. 代码与依赖
sudo -u crucible git clone <repo-url> /opt/crucible
cd /opt/crucible/backend
sudo -u crucible python3.11 -m venv .venv
sudo -u crucible .venv/bin/pip install -e .

# 2. 配置
sudo -u crucible cp .env.example .env
# 编辑 .env：DATABASE_URL/REDIS_URL 指宿主映射端口（5433/6380）；
# ENVIRONMENT=production；AUTH_SECRET 强随机；CORS 填实际域名

# 3. 基础设施 + agent-runner 镜像
cd /opt/crucible/infrastructure && sudo docker compose up -d
cd /opt/crucible && sudo docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .

# 4. 前端构建
cd /opt/crucible/frontend && npm ci && npm run build    # 产出 dist/

# 5. systemd 单元
sudo cp /opt/crucible/infrastructure/deploy/crucible-api.service /etc/systemd/system/
sudo cp /opt/crucible/infrastructure/deploy/crucible-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crucible-api crucible-worker

# 6. Nginx
sudo cp /opt/crucible/infrastructure/deploy/nginx-crucible.conf /etc/nginx/conf.d/crucible.conf
# 编辑 server_name / 证书路径
sudo nginx -t && sudo systemctl reload nginx
```

## 升级流程

```bash
sudo systemctl stop crucible-worker crucible-api
cd /opt/crucible && sudo -u crucible git pull
cd backend && sudo -u crucible .venv/bin/pip install -e .
sudo -u crucible .venv/bin/alembic upgrade head     # 有迁移时
cd ../frontend && sudo -u crucible npm ci && sudo -u crucible npm run build
# agent-runner / node-skills 有变更时重建镜像：
cd /opt/crucible && sudo docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .
sudo systemctl start crucible-api crucible-worker
```

升级窗口内 worker 停止即停止接新任务；运行中任务受 `acks_late` 保护，worker 重启后由 Celery 重派。

## 日常运维

```bash
journalctl -u crucible-api -f          # API 日志
journalctl -u crucible-worker -f       # worker / 编排日志
systemctl status crucible-worker       # 状态
docker ps --filter label=managed_by=crucible-agent-runner   # 活动沙箱
docker compose -p crucible-infra ps    # 基础设施
```

## 校验清单（上线前）

- [ ] `curl http://127.0.0.1:8010/health` 返回 200（或实际健康端点）
- [ ] `ENVIRONMENT=production` 下 API 正常启动（config 强制校验通过：AUTH_SECRET / PostgreSQL / CORS 非 `*`）
- [ ] worker 日志无 Docker 权限报错；`sudo -u crucible docker ps` 可用
- [ ] 建一个 Mock 任务跑通编排（沙箱拉起 → 事件落库 → 报告生成）
- [ ] Nginx：HTTPS 跳转、`/docs` 可开、SSE 事件流无缓冲（浏览器 Network 里 EventStream 帧持续到达）
- [ ] `.env` 权限 600，属主 crucible

## 已知边界（本形态）

- 单机：所有沙箱/靶场与本机 daemon 绑定，多机需走 runner-node 服务化（未来另立项）
- `PrivateTmp=false` 是刻意选择：任务工作区 `/tmp/crucible/*` 必须对 worker 与 Docker daemon 同时可见；隔离由 agent-runner 容器层（只读 rootfs、cap_drop ALL、非 root）承担
- worker 重启期间运行中任务会被 Celery 重派重跑（`acks_late` 语义），断点续跑按已完成的 NodeRun 复用
