---
name: db-migrate
description: Alembic 迁移生成与执行
---

# Database Migrate

规则摘要：`.cursor/rules/db-migrate.mdc`。命令一律用仓库根 `.venv`（见 `.cursor/rules/agent-env.mdc`）。

当前 **head**：`j3e6a7b18c42`（以 `alembic heads` / `_alembic_head()` 为准）。

## 1. 空库 / 升级

```bash
cd backend
../.venv/bin/alembic upgrade head
```

开发空库 API 启动时 `init_db()` 会：PostgreSQL 先 `alembic upgrade head`，再 `create_all` + 补缺失列/索引/注释并 stamp head。已有库也依赖启动时 upgrade，但生产仍应显式 `upgrade head`。

`DATABASE_URL` 只来自 `backend/.env`（`alembic/env.py` 注入），勿写进 `alembic.ini`。

## 2. 模型变更 → 新 revision

```bash
cd backend
../.venv/bin/alembic revision --autogenerate -m "add xxx"
# 或 hand-written revision
```

### 提交前清单（缺一项即不完整）

- [ ] `down_revision` = 当前 head
- [ ] 人工 review autogenerate（enum rename、列类型等常漏）
- [ ] 更新 `tests/test_schema_baseline.py`（新文件 + `_alembic_head()` 断言）
- [ ] 相关 model 测试列集合（如 `test_project_model.py`）
- [ ] `../.venv/bin/pytest tests/test_schema_baseline.py -q`

## 3. 链结构（勿改 baseline）

```
c18a0e9b4d21 (baseline)
  → b7e4c2a19f08
  → e8c3a1b047d2 ─┬→ f3a9c2d18e04 (merge)
  → a1b8c3d049e4 ─┘
  → d4b7e1c08a92 → e7d2b4a10c95 → f8c2a1b03d14
  → g7b3e9a02c15 → h1c4d8e05f26 → i2d5f6a07b31 → j3e6a7b18c42 (head)
```

## 4. 回滚

```bash
../.venv/bin/alembic downgrade -1
```

生产 `downgrade` / `drop` 需用户确认。

## 5. 允许 / 禁止

- **允许**：DDL 增量；独立 revision 做小数据回填（如 `openai_compat` → `custom`）或列注释同步
- **禁止**：改 baseline；migration 内调 Service；不可回滚的 destructive 变更未经确认
