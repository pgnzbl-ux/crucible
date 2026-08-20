---
name: test
description: 跑后端 / 前端测试与冒烟脚本
---

# Test

环境：Python 命令用仓库根 **`.venv/bin/`**（见 `.cursor/rules/agent-env.mdc`）。勿用系统 `python3` 误判无 pytest。

## 1. 沙箱冒烟（最快反馈）

```bash
cd backend && ../.venv/bin/python tests/smoke_agent_runner.py
```

## 2. 后端单元测试

```bash
cd backend && ../.venv/bin/pytest -x
# 或 activate 后：source ../.venv/bin/activate && pytest -q
```

pytest 覆盖 `DATABASE_URL` 为 sqlite（`tests/conftest.py`），不连 `.env` PostgreSQL。

## 3. 前端

```bash
cd frontend && npx tsc --noEmit
cd frontend && npm run test -- --run
cd frontend && npm run build
```

## 4. 迁移相关

```bash
cd backend && ../.venv/bin/pytest tests/test_schema_baseline.py -q
```

## 5. 全链路冒烟（P0 改动）

见 `.claude/skills/smoke`。

## 6. 注意

- `ENVIRONMENT=test`
- 凭据用 fixture，不复用 dev key
- 沙箱容器 tag 加 `test-` 前缀
