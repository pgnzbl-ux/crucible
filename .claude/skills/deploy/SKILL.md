---
name: deploy
description: 启动基础设施 + 后端 API + Celery worker + 前端 dev server 一条龙
---

# Deploy (development)

按 docs/development-guide.md §6.1 启动整套环境。

## 步骤

1. **基础设施**（postgres 5433 / redis 6380 / minio 9000）
   ```bash
   cd infrastructure && docker compose up -d
   ```
   等待 healthcheck 通过。

2. **沙箱镜像**（首次或更新 `sandbox/Dockerfile` 后）
   ```bash
   docker build -f infrastructure/sandbox/Dockerfile -t crucible-sandbox:base .
   ```

3. **后端**
   ```bash
   cd backend
   cp ../.env.example .env   # 首次
   pip install -e ".[dev]"
   python -m uvicorn app.main:app --port 8010
   ```

4. **Celery worker**（独立终端）
   ```bash
   cd backend && python run_worker.py
   ```
   Windows `--pool=solo` 已固定。

5. **前端**
   ```bash
   cd frontend && npm install && npm run dev
   ```
   5173 端口。

## 验证

```bash
curl http://localhost:8010/health
curl http://localhost:8010/metrics | head
```

## 端口冲突

- 5433 / 6380 / 9000 / 8010 / 5173 已被占用则改 docker-compose / .env / vite.config
- 不要改回 5432 / 6379（与宿主机默认端口冲突，参考 docs §5.4）