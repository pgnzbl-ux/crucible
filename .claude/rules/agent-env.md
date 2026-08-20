# Agent 命令与虚拟环境

与 `.cursor/rules/agent-env.mdc` 同源。

## 虚拟环境

- 路径：仓库根 `.venv/`（非 `backend/.venv`）
- Agent 跑 Python 命令优先：`.venv/bin/pytest`、`.venv/bin/alembic`、`.venv/bin/uvicorn`
- 勿用系统 `python3` 失败后断言「无 pytest」

## 常用

```bash
cd backend && ../.venv/bin/pytest -q
cd backend && ../.venv/bin/alembic upgrade head
cd backend && ../.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
```

开发 API 端口 **8010**（非 `python -m app.main` 默认 8000）。
