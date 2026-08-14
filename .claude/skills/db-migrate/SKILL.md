---
name: db-migrate
description: Alembic 迁移生成与执行
---

# Database Migrate

## 1. 自动生成迁移

```bash
cd backend
alembic revision --autogenerate -m "add xxx"
```

## 2. 检查生成的 upgrade / downgrade

- **必须**人工 review autogenerate 输出，alembic 不能识别所有变更（如 enum rename / column type）
- 跨 Context 新表必须放进对应 Context 的 `models.py`（worker import 注册 metadata，参考 `backend.md` §1）

## 3. 应用迁移

```bash
alembic upgrade head
```

开发 SQLite 用 `alembic upgrade head`；PostgreSQL 切 `DATABASE_URL` 后同样命令。

## 4. 回滚

```bash
alembic downgrade -1     # 回一版
alembic downgrade base   # 全回
```

生产回滚需用户确认（参考 `git-workflow.md` §5 不可逆操作）。

## 5. 注意事项

- 不要在迁移里塞业务代码 / 数据回填（用单独脚本）
- 不要 drop 列只做 `server_default` 移除，迁移历史要可回滚