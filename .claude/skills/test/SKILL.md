---
name: test
description: 跑后端 / 前端测试与冒烟脚本
---

# Test

## 1. 沙箱冒烟（最快反馈）

```bash
cd backend && python tests/smoke_sandbox.py
```

覆盖：容器创建 / exec / OOM / 网络隔离 / 清理。

## 2. 后端单元测试（待 P1 补）

```bash
cd backend && pytest -x
```

## 3. 前端类型检查 + 构建

```bash
cd frontend && npx tsc --noEmit
cd frontend && npm run build
```

## 4. 全链路冒烟（任意 P0 改动后跑一遍）

1. `POST /api/v1/auth/register` → `POST /api/v1/auth/login` 取 token
2. `POST /api/v1/tasks` 创建任务
3. 轮询 `GET /api/v1/tasks/{id}` 看 status 推进
4. `GET /api/v1/tasks/{id}/events` 看事件流
5. `GET /api/v1/reports/{id}` 确认报告生成

## 5. 注意事项

- 测试用 `ENVIRONMENT=test`（不走生产强校验）
- 凭据一律测试 fixture，不复用 dev key
- 沙箱容器 tag 加 `test-` 前缀避免与开发冲突