---
name: deploy
description: 启动基础设施 + 后端 API + Celery worker + 前端 dev server 一条龙
---

# Deploy (development)

按仓库根目录 `README.md`「快速开始」。宿主 Linux（含 WSL）。虚拟环境在**仓库根** `.venv/`，不要用系统 `python`。

## 首次（新机器 / 新克隆）

1. **Docker 可用**（WSL 先开 Docker Desktop，`docker ps` 能通）。
2. **基础设施**（postgres 5433 / redis 6380 / minio 9000）
   ```bash
   cd infrastructure && docker compose up -d
   docker compose ps   # 等 healthy
   ```
3. **Agent 镜像**（首次，或改了 `infrastructure/agent-runner/` / `node-skills` 之后）
   ```bash
   # 必须在仓库根构建
   docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .
   ```
4. **Python 环境**
   ```bash
   python3.12 -m venv .venv && source .venv/bin/activate
   cd backend
   cp .env.example .env
   pip install -e ".[dev]"
   pip install -r requirements.txt
   ```
5. **三个进程**（各开一个终端，均 `source .venv/bin/activate`）
   ```bash
   # API
   cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload

   # Worker（缺 gitleaks / osv-scanner / 本地 semgrep 规则树时写入当前 .venv）
   cd backend && python run_worker.py

   # 前端
   cd frontend && npm install && npm run dev
   ```

## WSL / 机器重启后

数据卷、`.env`、`.venv`、Agent 镜像都还在。不要重装依赖、不要重建镜像。

```bash
# Docker 起来之后
cd infrastructure && docker compose up -d

source .venv/bin/activate
# 终端 1
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
# 终端 2
cd backend && python run_worker.py
# 终端 3
cd frontend && npm run dev
```

## 验证

```bash
curl http://localhost:8010/health
# 控制台 http://localhost:5173
```

只开 API、不开 Worker 时，任务会停在队列里。

## 端口冲突

- 5433 / 6380 / 9000 / 8010 / 5173 已被占用则改 docker-compose / `.env` / vite.config
- 不要改回 5432 / 6379（与宿主机默认端口冲突）
